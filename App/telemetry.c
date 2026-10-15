#include "telemetry.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "dut_io.h"
#include "host_link.h"
#include "i2c.h"
#include "power.h"
#include "status.h"

#define I2C_TIMEOUT_MS 10

/* ---- INA237 ------------------------------------------------------------------ */

#define INA_CONFIG     0x00
#define INA_ADC_CONFIG 0x01
#define INA_VSHUNT     0x04
#define INA_VBUS       0x05
#define INA_DIAG_ALRT  0x0B
#define INA_SOVL       0x0C
#define INA_MFR_ID     0x3E

#define INA_MFR_TI     0x5449
#define INA_VBUS_LSB   3.125e-3f  /* V */
#define INA_VSH_LSB    5.0e-6f    /* V, ADCRANGE = 0 (+-163.84 mV) */

/* Continuous shunt + bus, 1052 us conversions, 16x averaging (~34 ms per update). */
#define INA_ADC_CONFIG_VALUE ((0xBU << 12) | (5U << 9) | (5U << 6) | (5U << 3) | 2U)
/* DIAG_ALRT: ALATCH (bit 15) so a trip stays visible until we read it. SLOWALERT stays 0, so
 * the limit is compared against every single (non-averaged) shunt conversion: a trip fires
 * within one ~2.1 ms shunt+bus cycle rather than after the 16x average. */
#define INA_DIAG_ALATCH (1U << 15)
#define INA_DIAG_SHNTOL (1U << 6)

/* Addresses from the A1/A0 straps; shunts are the 0.1 % parts on the schematic. */
static const struct {
  const char *name;
  uint8_t addr;
  float shunt_ohm;
} mons[MON_COUNT] = {
    [MON_VIO] = {"vio", 0x40, 2.0f},
    [MON_VBAT] = {"vbat", 0x41, 0.82f},
    [MON_VBAT_RF] = {"vbat_rf", 0x45, 1.8f},
    [MON_VDDD] = {"vddd", 0x43, 1.8f},
    [MON_VDDA] = {"vdda", 0x44, 5.6f},
    [MON_RF_VDD] = {"rf_vdd", 0x47, 1.8f},
};

static bool mon_present[MON_COUNT];
static float mon_ilim_ma[MON_COUNT];
static volatile bool alert_pending;

static bool reg_read16(uint8_t addr, uint8_t reg, uint16_t *val)
{
  uint8_t b[2];
  if (HAL_I2C_Mem_Read(&hi2c2, (uint16_t)(addr << 1), reg, I2C_MEMADD_SIZE_8BIT, b, 2,
                       I2C_TIMEOUT_MS) != HAL_OK) {
    return false;
  }
  *val = (uint16_t)((b[0] << 8) | b[1]);
  return true;
}

static bool reg_write16(uint8_t addr, uint8_t reg, uint16_t val)
{
  uint8_t b[2] = {(uint8_t)(val >> 8), (uint8_t)val};
  return HAL_I2C_Mem_Write(&hi2c2, (uint16_t)(addr << 1), reg, I2C_MEMADD_SIZE_8BIT, b, 2,
                           I2C_TIMEOUT_MS) == HAL_OK;
}

static void ina_init(mon_id_t id)
{
  uint16_t mfr = 0;
  uint8_t a = mons[id].addr;
  mon_present[id] = reg_read16(a, INA_MFR_ID, &mfr) && mfr == INA_MFR_TI;
  if (!mon_present[id]) {
    return;
  }
  reg_write16(a, INA_CONFIG, 0x0000);  /* ADCRANGE = 0 */
  reg_write16(a, INA_ADC_CONFIG, INA_ADC_CONFIG_VALUE);
  reg_write16(a, INA_SOVL, 0x7FFF);    /* trip disabled until `ilim` sets one */
  reg_write16(a, INA_DIAG_ALRT, INA_DIAG_ALATCH);
}

static bool ina_set_limit(mon_id_t id, float ma)
{
  uint32_t code = 0x7FFF;
  if (ma > 0.0f) {
    float v = ma * 1e-3f * mons[id].shunt_ohm;
    float c = v / INA_VSH_LSB;
    code = c > 32767.0f ? 0x7FFFU : (uint32_t)c;
  }
  return reg_write16(mons[id].addr, INA_SOVL, (uint16_t)code);
}

bool mon_read(mon_id_t id, mon_reading_t *r)
{
  uint16_t vb, vs;
  if (!mon_present[id] || !reg_read16(mons[id].addr, INA_VBUS, &vb) ||
      !reg_read16(mons[id].addr, INA_VSHUNT, &vs)) {
    return false;
  }
  r->vbus = (float)vb * INA_VBUS_LSB;
  r->vshunt = (float)(int16_t)vs * INA_VSH_LSB;
  r->current = r->vshunt / mons[id].shunt_ohm;
  return true;
}

int mon_find(const char *name)
{
  for (int i = 0; i < MON_COUNT; i++) {
    if (strcmp(name, mons[i].name) == 0) {
      return i;
    }
  }
  return -1;
}

const char *mon_name(mon_id_t id)
{
  return mons[id].name;
}

/* TELEM_ALERT (PA1) is shared, open-drain, active low. The only alert we enable is the
 * shunt over-limit, so treat any assertion as an over-current: drop the rails from
 * interrupt context first, then work out who tripped in the main loop. */
void HAL_GPIO_EXTI_Falling_Callback(uint16_t pin)
{
  if (pin == TELEM_ALERT_Pin) {
    power_emergency_off();
    status.ocp_trips++;
    alert_pending = true;
  } else if (pin == BTN_DUT_RST_Pin) {
    dut_reset_button();
  }
}

/* ---- TLA2528 --------------------------------------------------------------------- */

#define TLA_OP_READ  0x10
#define TLA_OP_WRITE 0x08
#define TLA_GENERAL_CFG 0x01
#define TLA_CHANNEL_SEL 0x11
#define TLA_VREF 3.3f   /* AVDD = SYS_3V3 is the reference */

static uint8_t tla_addr;  /* 0 = not found */

static const char *const aux_names[8] = {
    "VNFC", "PWR_BG", "PWR_TEST_0", "PWR_I_REF", "VIO", "PWR_VDD_RF", "SPARE_1", "SPARE_2",
};

static bool tla_write(uint8_t reg, uint8_t val)
{
  uint8_t b[3] = {TLA_OP_WRITE, reg, val};
  return HAL_I2C_Master_Transmit(&hi2c2, (uint16_t)(tla_addr << 1), b, 3, I2C_TIMEOUT_MS) ==
         HAL_OK;
}

static bool tla_read(uint8_t reg, uint8_t *val)
{
  uint8_t b[2] = {TLA_OP_READ, reg};
  return HAL_I2C_Master_Transmit(&hi2c2, (uint16_t)(tla_addr << 1), b, 2, I2C_TIMEOUT_MS) ==
             HAL_OK &&
         HAL_I2C_Master_Receive(&hi2c2, (uint16_t)(tla_addr << 1), val, 1, I2C_TIMEOUT_MS) ==
             HAL_OK;
}

static void tla_init(void)
{
  /* ADDR is strapped by R71/R72; probe the whole 0x10-0x17 range rather than trust it. */
  tla_addr = 0;
  for (uint8_t a = 0x10; a <= 0x17; a++) {
    if (HAL_I2C_IsDeviceReady(&hi2c2, (uint16_t)(a << 1), 2, I2C_TIMEOUT_MS) == HAL_OK) {
      tla_addr = a;
      break;
    }
  }
  if (tla_addr == 0) {
    return;
  }
  /* Offset calibration: set CAL and wait for it to self-clear. Pins default to analog
   * inputs and manual mode, which is what we want. */
  tla_write(TLA_GENERAL_CFG, 0x02);
  uint8_t cfg = 0x02;
  uint32_t t0 = millis();
  while ((cfg & 0x02) && millis() - t0 < 20) {
    tla_read(TLA_GENERAL_CFG, &cfg);
  }
}

static bool tla_convert(uint8_t ch, float *volts)
{
  uint8_t b[2];
  if (!tla_write(TLA_CHANNEL_SEL, ch)) {
    return false;
  }
  /* Manual mode: each read frame starts a conversion of the selected channel. The
   * first one after a mux change is thrown away. */
  for (int i = 0; i < 2; i++) {
    if (HAL_I2C_Master_Receive(&hi2c2, (uint16_t)(tla_addr << 1), b, 2, I2C_TIMEOUT_MS) !=
        HAL_OK) {
      return false;
    }
  }
  uint16_t code = (uint16_t)(((b[0] << 8) | b[1]) >> 4);
  *volts = (float)code * TLA_VREF / 4096.0f;
  return true;
}

/* ---- module ---------------------------------------------------------------------- */

void telemetry_init(void)
{
  for (int i = 0; i < MON_COUNT; i++) {
    ina_init((mon_id_t)i);
  }
  tla_init();
}

void telemetry_poll(void)
{
  if (!alert_pending) {
    return;
  }
  alert_pending = false;
  out("! ALERT: over-current trip, all DUT rails off:");
  for (int i = 0; i < MON_COUNT; i++) {
    uint16_t diag;
    if (mon_present[i] && reg_read16(mons[i].addr, INA_DIAG_ALRT, &diag) &&
        (diag & INA_DIAG_SHNTOL)) {
      status.ocp_rails |= 1U << i;
      out(" %s", mons[i].name);
    }
  }
  out("\n");
}

bool cmd_tele(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  out("rail      addr  shunt     Vbus      I         Vshunt\n");
  for (int i = 0; i < MON_COUNT; i++) {
    mon_reading_t r;
    if (!mon_read((mon_id_t)i, &r)) {
      out("%-9s 0x%02X  %5.2f ohm  (no response)\n", mons[i].name, mons[i].addr,
          (double)mons[i].shunt_ohm);
      continue;
    }
    out("%-9s 0x%02X  %5.2f ohm  %7.4f V  %8.3f mA  %8.3f mV\n", mons[i].name, mons[i].addr,
        (double)mons[i].shunt_ohm, (double)r.vbus, (double)(r.current * 1e3f),
        (double)(r.vshunt * 1e3f));
  }
  return true;
}

bool cmd_ilim(int argc, char **argv)
{
  if (argc == 1) {
    for (int i = 0; i < MON_COUNT; i++) {
      out("%-9s %s", mons[i].name, mon_ilim_ma[i] > 0.0f ? "" : "off\n");
      if (mon_ilim_ma[i] > 0.0f) {
        out("%.2f mA\n", (double)mon_ilim_ma[i]);
      }
    }
    return true;
  }
  float ma;
  int id = argc == 3 ? mon_find(argv[1]) : -1;
  if (id < 0 || !arg_float(argv[2], &ma) || ma < 0.0f) {
    return cmd_err("usage: ilim <vio|vbat|vbat_rf|vddd|vdda|rf_vdd> <mA>");
  }
  if (!mon_present[id] || !ina_set_limit((mon_id_t)id, ma)) {
    return cmd_err("%s monitor not responding", mons[id].name);
  }
  mon_ilim_ma[id] = ma;
  return true;
}

bool cmd_aux(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  if (tla_addr == 0) {
    tla_init();
    if (tla_addr == 0) {
      return cmd_err("TLA2528 not found at 0x10-0x17");
    }
  }
  for (uint8_t ch = 0; ch < 8; ch++) {
    float v;
    if (tla_convert(ch, &v)) {
      out("AIN%u %-11s %6.4f V\n", ch, aux_names[ch], (double)v);
    } else {
      out("AIN%u %-11s (read failed)\n", ch, aux_names[ch]);
    }
  }
  return true;
}

bool cmd_i2c(int argc, char **argv)
{
  if (argc != 2 || strcmp(argv[1], "scan") != 0) {
    return cmd_err("usage: i2c scan");
  }
  for (uint8_t a = 0x08; a < 0x78; a++) {
    if (HAL_I2C_IsDeviceReady(&hi2c2, (uint16_t)(a << 1), 1, 2) == HAL_OK) {
      out("0x%02X\n", a);
    }
  }
  return true;
}
