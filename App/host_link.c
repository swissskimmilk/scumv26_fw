#include "host_link.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "cmd.h"
#include "status.h"
#include "tusb.h"

#define LINE_MAX 160

static char line[LINE_MAX];
static uint32_t line_len;
static bool line_overflow;

/* ---- USB hardware bring-up (TinyUSB owns the OTG_FS core) ---------------- */

static void usb_hw_init(void)
{
  /* 48 MHz kernel clock from HSI48, trimmed to the host's SOF by the CRS. */
  RCC_OscInitTypeDef osc = {0};
  osc.OscillatorType = RCC_OSCILLATORTYPE_HSI48;
  osc.HSI48State = RCC_HSI48_ON;
  osc.PLL.PLLState = RCC_PLL_NONE;
  HAL_RCC_OscConfig(&osc);

  RCC_PeriphCLKInitTypeDef clk = {0};
  clk.PeriphClockSelection = RCC_PERIPHCLK_CLK48;
  clk.IclkClockSelection = RCC_CLK48CLKSOURCE_HSI48;
  HAL_RCCEx_PeriphCLKConfig(&clk);

  __HAL_RCC_CRS_CLK_ENABLE();
  RCC_CRSInitTypeDef crs = {0};
  crs.Prescaler = RCC_CRS_SYNC_DIV1;
  crs.Source = RCC_CRS_SYNC_SOURCE_USB;
  crs.Polarity = RCC_CRS_SYNC_POLARITY_RISING;
  crs.ReloadValue = __HAL_RCC_CRS_RELOADVALUE_CALCULATE(48000000, 1000);
  crs.ErrorLimitValue = RCC_CRS_ERRORLIMIT_DEFAULT;
  crs.HSI48CalibrationValue = RCC_CRS_HSI48CALIBRATION_DEFAULT;
  HAL_RCCEx_CRSConfig(&crs);

  /* PA11/PA12 = OTG_FS DM/DP. PA9 is USART1_TX here, so VBUS sensing stays off. */
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF10_USB;
  HAL_GPIO_Init(GPIOA, &gpio);

  /* VDDUSB comes from SYS_3V3; tell the PWR block it is valid. */
  __HAL_RCC_PWR_CLK_ENABLE();
  HAL_PWREx_EnableVddUSB();
  __HAL_RCC_USB_OTG_FS_CLK_ENABLE();

  USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBDEN;
  USB_OTG_FS->GOTGCTL |= USB_OTG_GOTGCTL_BVALOEN | USB_OTG_GOTGCTL_BVALOVAL;

  HAL_NVIC_SetPriority(OTG_FS_IRQn, 5, 0);
}

void OTG_FS_IRQHandler(void)
{
  tud_int_handler(0);
}

uint32_t tusb_time_millis_api(void)
{
  return HAL_GetTick();
}

/* ---- public API ------------------------------------------------------------ */

void host_link_init(void)
{
  usb_hw_init();
  tusb_rhport_init_t dev = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_FULL};
  tusb_init(0, &dev);
}

bool host_connected(void)
{
  return tud_cdc_connected();
}

void host_link_service(void)
{
  tud_task();
}

static void flush_blocking(uint32_t need)
{
  /* Keep servicing USB until there is room; drop output if nobody is listening. */
  while (tud_cdc_write_available() < need) {
    tud_task();
    tud_cdc_write_flush();
    if (!tud_cdc_connected()) {
      return;
    }
  }
}

void out_write(const void *data, uint32_t len)
{
  const uint8_t *p = data;
  if (!tud_cdc_connected()) {
    status.host_dropped += len;
    return;
  }
  while (len > 0U && tud_cdc_connected()) {
    uint32_t avail = tud_cdc_write_available();
    if (avail == 0U) {
      flush_blocking(1);
      continue;
    }
    uint32_t n = len < avail ? len : avail;
    tud_cdc_write(p, n);
    p += n;
    len -= n;
  }
  tud_cdc_write_flush();
}

void out(const char *fmt, ...)
{
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  if (n <= 0) {
    return;
  }
  if ((uint32_t)n >= sizeof buf) {
    n = sizeof buf - 1;
  }
  out_write(buf, (uint32_t)n);
}

void host_link_poll(void)
{
  tud_task();
  PIN_SET(LED_HOST, tud_cdc_connected());

  while (tud_cdc_available()) {
    char c = (char)tud_cdc_read_char();
    if (c == '\r' || c == '\n') {
      if (line_overflow) {
        out("ERR line too long\n");
      } else if (line_len > 0U) {
        line[line_len] = '\0';
        cmd_execute(line);
      }
      line_len = 0;
      line_overflow = false;
    } else if (line_len < LINE_MAX - 1U) {
      line[line_len++] = c;
    } else {
      line_overflow = true;
    }
  }
}
