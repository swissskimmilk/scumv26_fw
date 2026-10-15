#include "adc_capture.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "dcmi.h"
#include "host_link.h"
#include "spi.h"
#include "status.h"

/* ---- capture buffer ----------------------------------------------------------------
 * DCMI in 10-bit mode packs two samples per 32-bit word (first sample in bits 9:0,
 * second in bits 25:16). 96 Ki words = 384 KiB = 192 Ki samples of one channel, about
 * 6 ms at 32 MS/s. */
#define CAP_MAX_WORDS (96U * 1024U)
#define NODE_WORDS    8192U /* 32 KiB per linked-list node (GPDMA block limit is 64 KiB) */
#define MAX_NODES     ((CAP_MAX_WORDS + NODE_WORDS - 1U) / NODE_WORDS)

static uint32_t cap_buf[CAP_MAX_WORDS] __attribute__((aligned(32)));

/* ---- ADC3910 registers ---------------------------------------------------------------- */
#define REG_RESET          0x000
#define REG_CFG_ALERT      0x038
#define REG_SPARE          0x039
#define REG_TEST_PAT_CFG   0x091
#define REG_TEST_PAT_B_LO  0x092
#define REG_TEST_PAT_A_LO  0x094
#define REG_PDN_CFG        0x08C
#define SDR_CHB_SEL        (1U << 2)  /* SDR outputs channel B instead of A */
#define REG_CLK_CFG3       0x08E
#define DCLK_SYNC_MASK     (3U << 6)
#define DCLK_SYNC_SDR      (2U << 6)  /* "SDR clocking"; the datasheet misprints it as 1010b */
#define REG_INTERFACE_CFG1 0x098
#define SDR_EN             (1U << 4)
#define TEST_PAT_A         (1U << 4)
#define TEST_PAT_B         (1U << 5)

/* One channel per capture. The dual part cannot put both channels on rising edges at the
 * full rate (TI E2E 1490292), so we pick a channel:
 *   ddr-a / ddr-b  ADC left in its reset-default DDR; DCMI latches on DCLK rising (A) or
 *                  falling (B). Works even before any SPI configuration.
 *   sdr-a / sdr-b  ADC in SDR with SDR_CHB_SEL choosing the channel; DCMI always on rising.
 *                  Each sample is held for a whole DCLK period instead of half, so this
 *                  has twice the timing margin. Preferred once SPI is confirmed working. */
typedef enum { MODE_DDR_A, MODE_DDR_B, MODE_SDR_A, MODE_SDR_B } cap_mode_t;
static const char *const mode_names[] = {"ddr-a", "ddr-b", "sdr-a", "sdr-b"};
#define N_MODES 4
static cap_mode_t mode = MODE_DDR_A;

static DMA_HandleTypeDef hdma_dcmi;
static DMA_QListTypeDef dcmi_queue;
static DMA_NodeTypeDef dcmi_nodes[MAX_NODES];
static volatile bool dma_done;
static volatile bool dma_error;

/* ---- SPI (3-wire, half-duplex): R/W + A[14:0] + D[7:0], MSB first, SEN active low ---- */

static bool adc_wr(uint16_t addr, uint8_t val)
{
  uint8_t f[3] = {(uint8_t)((addr >> 8) & 0x7FU), (uint8_t)addr, val};
  PIN_SET(ADC_SEN, 0);
  bool ok = HAL_SPI_Transmit(&hspi1, f, 3, 10) == HAL_OK;
  PIN_SET(ADC_SEN, 1);
  return ok;
}

static bool adc_rd(uint16_t addr, uint8_t *val)
{
  uint8_t f[2] = {(uint8_t)(0x80U | ((addr >> 8) & 0x7FU)), (uint8_t)addr};
  PIN_SET(ADC_SEN, 0);
  bool ok = HAL_SPI_Transmit(&hspi1, f, 2, 10) == HAL_OK &&
            HAL_SPI_Receive(&hspi1, val, 1, 10) == HAL_OK;
  PIN_SET(ADC_SEN, 1);
  return ok;
}

static bool adc_reset(void)
{
  PIN_SET(ADC_RESET, 1); /* active high */
  delay_us(10);
  PIN_SET(ADC_RESET, 0);
  /* Ready after 2000 sample clocks (62.5 us at 32 MHz); CFG_ALERT confirms it. */
  uint8_t v = 0;
  uint32_t t0 = millis();
  while (millis() - t0 < 10U) {
    if (adc_rd(REG_CFG_ALERT, &v) && (v & 1U)) {
      return true;
    }
  }
  return false;
}

/* SPI sanity check: the spare register reads back what we write. */
static bool adc_spi_check(void)
{
  uint8_t v = 0;
  return adc_wr(REG_SPARE, 0xA5) && adc_rd(REG_SPARE, &v) && v == 0xA5 &&
         adc_wr(REG_SPARE, 0x5A) && adc_rd(REG_SPARE, &v) && v == 0x5A;
}

static bool update_bits(uint16_t addr, uint8_t mask, uint8_t bits)
{
  uint8_t v;
  return adc_rd(addr, &v) && adc_wr(addr, (uint8_t)((v & ~mask) | (bits & mask)));
}

static bool apply_mode(cap_mode_t m)
{
  bool sdr = m == MODE_SDR_A || m == MODE_SDR_B;
  if (!update_bits(REG_INTERFACE_CFG1, SDR_EN, sdr ? SDR_EN : 0U) ||
      !update_bits(REG_CLK_CFG3, DCLK_SYNC_MASK, sdr ? DCLK_SYNC_SDR : 0U) ||
      !update_bits(REG_PDN_CFG, SDR_CHB_SEL, m == MODE_SDR_B ? SDR_CHB_SEL : 0U)) {
    return false;
  }
  CLEAR_BIT(DCMI->CR, DCMI_CR_ENABLE);
  if (m == MODE_DDR_B) {
    CLEAR_BIT(DCMI->CR, DCMI_CR_PCKPOL); /* capture on falling edges */
  } else {
    SET_BIT(DCMI->CR, DCMI_CR_PCKPOL);
  }
  mode = m;
  return true;
}

/* ---- DMA: DCMI DR -> SRAM through a chain of linked-list nodes, no CPU in the loop ---- */

static void dma_cplt(DMA_HandleTypeDef *h)
{
  (void)h;
  dma_done = true;
}

static void dma_err(DMA_HandleTypeDef *h)
{
  (void)h;
  dma_error = true;
}

void GPDMA1_Channel12_IRQHandler(void)
{
  HAL_DMA_IRQHandler(&hdma_dcmi);
}

static bool dma_init(void)
{
  __HAL_RCC_GPDMA1_CLK_ENABLE();
  /* Channel 12: one of the four GPDMA channels with a 32-byte FIFO, so the SRAM side
   * can burst while the DCMI side is serviced word by word. */
  hdma_dcmi.Instance = GPDMA1_Channel12;
  hdma_dcmi.InitLinkedList.Priority = DMA_HIGH_PRIORITY;
  hdma_dcmi.InitLinkedList.LinkStepMode = DMA_LSM_FULL_EXECUTION;
  hdma_dcmi.InitLinkedList.LinkAllocatedPort = DMA_LINK_ALLOCATED_PORT1;
  hdma_dcmi.InitLinkedList.TransferEventMode = DMA_TCEM_LAST_LL_ITEM_TRANSFER;
  hdma_dcmi.InitLinkedList.LinkedListMode = DMA_LINKEDLIST_NORMAL;
  if (HAL_DMAEx_List_Init(&hdma_dcmi) != HAL_OK) {
    return false;
  }
  hdma_dcmi.XferCpltCallback = dma_cplt;
  hdma_dcmi.XferErrorCallback = dma_err;
  HAL_NVIC_SetPriority(GPDMA1_Channel12_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(GPDMA1_Channel12_IRQn);
  return true;
}

static bool dma_build(uint32_t words)
{
  if (dcmi_queue.Head != NULL) {
    HAL_DMAEx_List_UnLinkQ(&hdma_dcmi);
    HAL_DMAEx_List_ResetQ(&dcmi_queue);
  }
  DMA_NodeConfTypeDef nc = {0};
  nc.NodeType = DMA_GPDMA_LINEAR_NODE;
  nc.Init.Request = GPDMA1_REQUEST_DCMI_PSSI;
  nc.Init.BlkHWRequest = DMA_BREQ_SINGLE_BURST;
  nc.Init.Direction = DMA_PERIPH_TO_MEMORY;
  nc.Init.SrcInc = DMA_SINC_FIXED;
  nc.Init.DestInc = DMA_DINC_INCREMENTED;
  nc.Init.SrcDataWidth = DMA_SRC_DATAWIDTH_WORD;
  nc.Init.DestDataWidth = DMA_DEST_DATAWIDTH_WORD;
  nc.Init.SrcBurstLength = 1;
  nc.Init.DestBurstLength = 4;
  nc.Init.TransferAllocatedPort = DMA_SRC_ALLOCATED_PORT0 | DMA_DEST_ALLOCATED_PORT1;
  nc.Init.TransferEventMode = DMA_TCEM_BLOCK_TRANSFER;
  nc.Init.Mode = DMA_NORMAL;
  nc.DataHandlingConfig.DataExchange = DMA_EXCHANGE_NONE;
  nc.DataHandlingConfig.DataAlignment = DMA_DATA_RIGHTALIGN_ZEROPADDED;
  nc.TriggerConfig.TriggerPolarity = DMA_TRIG_POLARITY_MASKED;
  nc.SrcAddress = (uint32_t)&DCMI->DR;

  uint32_t done = 0;
  for (uint32_t i = 0; done < words; i++) {
    uint32_t n = words - done < NODE_WORDS ? words - done : NODE_WORDS;
    nc.DstAddress = (uint32_t)&cap_buf[done];
    nc.DataSize = n * 4U;
    if (HAL_DMAEx_List_BuildNode(&nc, &dcmi_nodes[i]) != HAL_OK ||
        HAL_DMAEx_List_InsertNode_Tail(&dcmi_queue, &dcmi_nodes[i]) != HAL_OK) {
      return false;
    }
    done += n;
  }
  return HAL_DMAEx_List_LinkQ(&hdma_dcmi, &dcmi_queue) == HAL_OK;
}

/* Frame strobes, looped back on the board into DCMI HSYNC/VSYNC. Both polarities are
 * "high = blanking", so data is taken only while both strobes are low. */
static void frame(bool active)
{
  if (active) {
    PIN_SET(ADC_HSYNC_DRV, 0);
    PIN_SET(ADC_VSYNC_DRV, 0); /* VSYNC leaving blanking = start of frame */
  } else {
    PIN_SET(ADC_VSYNC_DRV, 1);
    PIN_SET(ADC_HSYNC_DRV, 1);
  }
}

static bool capture(uint32_t words, uint32_t *got_ms)
{
  if (!dma_build(words)) {
    return cmd_err("DMA list build failed");
  }
  dma_done = false;
  dma_error = false;
  frame(false);

  DCMI->ICR = DCMI_ICR_OVR_ISC | DCMI_ICR_ERR_ISC | DCMI_ICR_FRAME_ISC;
  SET_BIT(DCMI->CR, DCMI_CR_CM); /* snapshot: one frame, then stop */
  SET_BIT(DCMI->CR, DCMI_CR_ENABLE);
  if (HAL_DMAEx_List_Start_IT(&hdma_dcmi) != HAL_OK) {
    CLEAR_BIT(DCMI->CR, DCMI_CR_ENABLE);
    return cmd_err("DMA start failed");
  }
  SET_BIT(DCMI->CR, DCMI_CR_CAPTURE);
  uint32_t t0 = millis();
  frame(true);

  /* 96 Ki words take ~6 ms at 32 MS/s; anything near 100 ms means DCLK is missing. */
  while (!dma_done && !dma_error && millis() - t0 < 100U) {
  }
  frame(false);
  *got_ms = millis() - t0;
  uint32_t ris = DCMI->RISR;
  CLEAR_BIT(DCMI->CR, DCMI_CR_CAPTURE);
  CLEAR_BIT(DCMI->CR, DCMI_CR_ENABLE);

  if (!dma_done) {
    HAL_DMA_Abort(&hdma_dcmi);
    status.capture_errors++;
    return cmd_err(dma_error ? "DMA transfer error" : "timeout: no ADC DCLK, or strobes not looped back");
  }
  if (ris & DCMI_RIS_OVR_RIS) {
    status.capture_errors++;
    return cmd_err("DCMI overrun: the DMA fell behind, so samples were lost");
  }
  return true;
}

/* CRC-32 (IEEE, as zlib.crc32) so the host can check the dump end to end. Table-driven:
 * ~12 ms for a full 384 KiB buffer, small next to the ~0.4 s USB transfer. */
static uint32_t crc32(const void *data, uint32_t len)
{
  static uint32_t table[256];
  if (table[1] == 0U) {
    for (uint32_t i = 0; i < 256U; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++) {
        c = (c & 1U) ? 0xEDB88320U ^ (c >> 1) : c >> 1;
      }
      table[i] = c;
    }
  }
  const uint8_t *p = data;
  uint32_t crc = 0xFFFFFFFFU;
  while (len--) {
    crc = table[(crc ^ *p++) & 0xFFU] ^ (crc >> 8);
  }
  return crc ^ 0xFFFFFFFFU;
}

/* ---- commands ---------------------------------------------------------------------------- */

void adc_capture_init(void)
{
  dma_init();
  /* Leave the ADC itself alone until `adc init`: its rails and clock may not be up yet. */
}

bool cmd_adc(int argc, char **argv)
{
  if (argc < 2) {
    return cmd_err("usage: adc init|reset|rd <a>|wr <a> <v>|mode ddr-a|ddr-b|sdr-a|sdr-b|pat <a> <b>|pat off");
  }
  const char *sub = argv[1];
  uint32_t a, v;
  if (!strcmp(sub, "init") || !strcmp(sub, "reset")) {
    if (!adc_reset()) {
      return cmd_err("ADC did not report ready (CFG_ALERT); check CLK and SPI");
    }
    if (!adc_spi_check()) {
      return cmd_err("SPI read-back of the spare register failed");
    }
    if (!apply_mode(mode)) {
      return cmd_err("SPI write failed");
    }
    out("ADC3910 ready, mode %s\n", mode_names[mode]);
    return true;
  }
  if (!strcmp(sub, "rd") && argc == 3 && arg_u32(argv[2], &a) && a < 0x8000U) {
    uint8_t val;
    if (!adc_rd((uint16_t)a, &val)) {
      return cmd_err("SPI error");
    }
    out("0x%03lX = 0x%02X\n", a, val);
    return true;
  }
  if (!strcmp(sub, "wr") && argc == 4 && arg_u32(argv[2], &a) && arg_u32(argv[3], &v) &&
      a < 0x8000U && v < 0x100U) {
    return adc_wr((uint16_t)a, (uint8_t)v) ? true : cmd_err("SPI error");
  }
  if (!strcmp(sub, "mode") && argc == 3) {
    for (int m = 0; m < N_MODES; m++) {
      if (!strcmp(argv[2], mode_names[m])) {
        return apply_mode((cap_mode_t)m) ? true : cmd_err("SPI error");
      }
    }
    return cmd_err("modes: ddr-a, ddr-b, sdr-a, sdr-b");
  }
  if (!strcmp(sub, "pat") && argc == 3 && !strcmp(argv[2], "off")) {
    return adc_wr(REG_TEST_PAT_CFG, 0) ? true : cmd_err("SPI error");
  }
  if (!strcmp(sub, "pat") && argc == 4 && arg_u32(argv[2], &a) && arg_u32(argv[3], &v) &&
      a < 0x4000U && v < 0x4000U) {
    /* Bypass-mode constant patterns (14-bit registers); a different value per channel
     * makes A and B easy to tell apart in a capture. */
    bool ok = adc_wr(REG_TEST_PAT_A_LO, (uint8_t)a) &&
              adc_wr(REG_TEST_PAT_A_LO + 1, (uint8_t)(a >> 8)) &&
              adc_wr(REG_TEST_PAT_B_LO, (uint8_t)v) &&
              adc_wr(REG_TEST_PAT_B_LO + 1, (uint8_t)(v >> 8)) &&
              adc_wr(REG_TEST_PAT_CFG, TEST_PAT_A | TEST_PAT_B);
    return ok ? true : cmd_err("SPI error");
  }
  return cmd_err("bad adc command");
}

bool cmd_cap(int argc, char **argv)
{
  uint32_t words;
  if (argc != 2 || !arg_u32(argv[1], &words) || words == 0 || words > CAP_MAX_WORDS) {
    return cmd_err("usage: cap <1-%u words>", CAP_MAX_WORDS);
  }
  uint32_t ms;
  if (!capture(words, &ms)) {
    return false;
  }
  /* Header line (bytes, mode, capture ms, CRC-32 of the data), then exactly that many raw
   * little-endian bytes, then OK. */
  out("BIN %lu %s %lu %08lX\n", (unsigned long)(words * 4U), mode_names[mode],
      (unsigned long)ms, (unsigned long)crc32(cap_buf, words * 4U));
  out_write(cap_buf, words * 4U);
  return true;
}
