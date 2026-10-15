#include "power.h"

#include <math.h>
#include <string.h>

#include "board.h"
#include "cmd.h"
#include "dac.h"
#include "host_link.h"
#include "telemetry.h"

/* VBAT trim network (TPS7A90 FB = 0.8 V): R18 top, R21 bottom, R19 + R20 from the DAC.
 *   VBAT = VFB + R_TOP * (VFB / R_BOT - (VDAC - VFB) / R_DAC)
 * DAC = VFB leaves the divider alone: 1.384 V. The DAC reference is VREF+ = MCU_VDDA =
 * 1.8 V, but the output buffer (needed: unbuffered, the DAC's ~10 kohm source impedance
 * would sit in series with R_DAC) only swings 0.2 V to VREF+ - 0.2 V. That limits VBAT
 * to about 1.16-1.55 V instead of the network's full 1.11-1.60 V. */
#define VFB       0.8f
#define R_TOP     10.0e3f
#define R_BOT     13.7e3f
#define R_DAC     36.4e3f
#define DAC_VREF  1.8f
#define DAC_MAX   4095U
#define DAC_V_MIN 0.2f
#define DAC_V_MAX (DAC_VREF - 0.2f)

/* Regulator soft-start (100 nF NR/SS) is a few ms; leave margin before checking. */
#define SEQ_SETTLE_MS 20

static uint32_t dac_code;

static float vbat_from_dac(float vdac)
{
  return VFB + R_TOP * (VFB / R_BOT - (vdac - VFB) / R_DAC);
}

static float dac_from_vbat(float vbat)
{
  return VFB + R_DAC * (VFB / R_BOT - (vbat - VFB) / R_TOP);
}

static void dac_write(uint32_t code)
{
  if (code > DAC_MAX) {
    code = DAC_MAX;
  }
  dac_code = code;
  HAL_DAC_SetValue(&hdac1, DAC_CHANNEL_1, DAC_ALIGN_12B_R, code);
}

static float dac_volts(void)
{
  return (float)dac_code * DAC_VREF / (float)DAC_MAX;
}

bool power_vbat_set(float volts)
{
  float vdac = dac_from_vbat(volts);
  if (vdac < DAC_V_MIN || vdac > DAC_V_MAX) {
    return false;
  }
  dac_write((uint32_t)lroundf(vdac / DAC_VREF * (float)DAC_MAX));
  return true;
}

void power_init(void)
{
  /* Park the DAC at VFB so VBAT sits at the divider's own 1.384 V. */
  HAL_DAC_Start(&hdac1, DAC_CHANNEL_1);
  dac_write((uint32_t)lroundf(VFB / DAC_VREF * (float)DAC_MAX));
}

void power_emergency_off(void)
{
  PIN_SET(VDD_EN, 0);
  PIN_SET(VBAT_EN, 0);
  PIN_SET(VIO_EN, 0);
  PIN_SET(LED_DUT, 0);
}

void power_poll(void)
{
  PIN_SET(LED_DUT, PIN_OUT_STATE(VIO_EN) || PIN_OUT_STATE(VBAT_EN) || PIN_OUT_STATE(VDD_EN));
}

/* ---- sequencing ------------------------------------------------------------------- */

static void report(mon_id_t id)
{
  mon_reading_t r;
  if (mon_read(id, &r)) {
    out("  %-8s %.3f V  %.3f mA\n", mon_name(id), (double)r.vbus, (double)(r.current * 1e3f));
  } else {
    out("  %-8s (monitor not responding)\n", mon_name(id));
  }
}

static bool power_up(void)
{
  /* VIO first so the DUT's I/O ring is biased before its core rails appear. */
  PIN_SET(VIO_EN, 1);
  HAL_Delay(SEQ_SETTLE_MS);
  mon_reading_t r;
  if (mon_read(MON_VIO, &r) && r.vbus < 1.6f) {
    power_emergency_off();
    return cmd_err("VIO only reached %.3f V; everything is off again", (double)r.vbus);
  }
  report(MON_VIO);

  PIN_SET(VBAT_EN, 1);
  HAL_Delay(SEQ_SETTLE_MS);
  report(MON_VBAT);
  report(MON_VBAT_RF);

  PIN_SET(VDD_EN, 1);
  HAL_Delay(SEQ_SETTLE_MS);
  report(MON_VDDD);
  report(MON_VDDA);
  report(MON_RF_VDD);
  return true;
}

static void power_down(void)
{
  PIN_SET(VDD_EN, 0);
  HAL_Delay(SEQ_SETTLE_MS);
  PIN_SET(VBAT_EN, 0);
  HAL_Delay(SEQ_SETTLE_MS);
  PIN_SET(VIO_EN, 0);
}

bool cmd_power(int argc, char **argv)
{
  if (argc == 2 && !strcmp(argv[1], "up")) {
    return power_up();
  }
  if (argc == 2 && !strcmp(argv[1], "down")) {
    power_down();
    return true;
  }
  return cmd_err("usage: power up|down");
}

bool cmd_rail(int argc, char **argv)
{
  if (argc == 1) {
    out("vio  %s\nvbat %s\n1v   %s\n", PIN_OUT_STATE(VIO_EN) ? "on" : "off",
        PIN_OUT_STATE(VBAT_EN) ? "on" : "off", PIN_OUT_STATE(VDD_EN) ? "on" : "off");
    return true;
  }
  bool on;
  if (argc != 3 || !arg_onoff(argv[2], &on)) {
    return cmd_err("usage: rail vio|vbat|1v on|off");
  }
  if (!strcmp(argv[1], "vio")) {
    PIN_SET(VIO_EN, on);
  } else if (!strcmp(argv[1], "vbat")) {
    PIN_SET(VBAT_EN, on);
  } else if (!strcmp(argv[1], "1v")) {
    PIN_SET(VDD_EN, on);
  } else {
    return cmd_err("unknown rail '%s'", argv[1]);
  }
  return true;
}

/* ---- rail-source switches (TS3A24159) --------------------------------------------- */
/* Control high = NO = the board regulator drives the rail. Control low = NC: VDDD/VDDA
 * are left to the DUT's own LDOs, and RF_VDD is fed from the DUT's RF LDO output (G9). */

bool cmd_sw(int argc, char **argv)
{
  if (argc == 1) {
    out("vddd %s\nvdda %s\nrf   %s\n", PIN_OUT_STATE(VDDD_SW) ? "board" : "dut",
        PIN_OUT_STATE(VDDA_SW) ? "board" : "dut", PIN_OUT_STATE(RF_VDD_SW) ? "board" : "dut");
    return true;
  }
  if (argc != 3 || (strcmp(argv[2], "board") && strcmp(argv[2], "dut"))) {
    return cmd_err("usage: sw vddd|vdda|rf board|dut");
  }
  bool board = !strcmp(argv[2], "board");
  if (!strcmp(argv[1], "vddd")) {
    PIN_SET(VDDD_SW, board);
  } else if (!strcmp(argv[1], "vdda")) {
    PIN_SET(VDDA_SW, board);
  } else if (!strcmp(argv[1], "rf")) {
    PIN_SET(RF_VDD_SW, board);
  } else {
    return cmd_err("unknown switch '%s'", argv[1]);
  }
  return true;
}

/* ---- VBAT trim ---------------------------------------------------------------------- */

bool cmd_vbat(int argc, char **argv)
{
  if (argc == 2) {
    float v;
    if (!arg_float(argv[1], &v)) {
      return cmd_err("usage: vbat [volts]");
    }
    if (!power_vbat_set(v)) {
      return cmd_err("%.3f V is outside the trim range %.3f-%.3f V", (double)v,
                     (double)vbat_from_dac(DAC_V_MAX), (double)vbat_from_dac(DAC_V_MIN));
    }
  }
  out("dac %lu (%.4f V) -> VBAT set point %.4f V\n", dac_code, (double)dac_volts(),
      (double)vbat_from_dac(dac_volts()));
  return true;
}

bool cmd_dac(int argc, char **argv)
{
  if (argc == 2) {
    uint32_t code;
    if (!arg_u32(argv[1], &code) || code > DAC_MAX) {
      return cmd_err("usage: dac [0-4095]");
    }
    dac_write(code);
  }
  out("dac %lu (%.4f V) -> VBAT set point %.4f V\n", dac_code, (double)dac_volts(),
      (double)vbat_from_dac(dac_volts()));
  return true;
}

/* Close the loop on the INA237 reading at the DUT pin, which includes the shunt drop and
 * the resistor tolerances that the open-loop formula ignores. */
bool cmd_trim(int argc, char **argv)
{
  float target;
  if (argc != 2 || !arg_float(argv[1], &target)) {
    return cmd_err("usage: trim <volts>");
  }
  if (!PIN_OUT_STATE(VBAT_EN)) {
    return cmd_err("VBAT is off");
  }
  if (!power_vbat_set(target)) {
    return cmd_err("%.3f V is outside the trim range", (double)target);
  }
  /* dVBAT/dDAC = -R_TOP / R_DAC */
  const float gain = -R_TOP / R_DAC;
  mon_reading_t r = {0};
  for (int i = 0; i < 8; i++) {
    HAL_Delay(80); /* two INA237 averaging windows */
    if (!mon_read(MON_VBAT, &r)) {
      return cmd_err("VBAT monitor not responding");
    }
    float err = target - r.vbus;
    out("  iter %d: dac %lu  VBAT %.4f V  err %+.1f mV\n", i, dac_code, (double)r.vbus,
        (double)(err * 1e3f));
    if (fabsf(err) < 1.0e-3f) {
      return true;
    }
    float step = err / gain / DAC_VREF * (float)DAC_MAX;
    int32_t next = (int32_t)dac_code + (int32_t)lroundf(step);
    const int32_t lo = (int32_t)(DAC_V_MIN / DAC_VREF * (float)DAC_MAX);
    const int32_t hi = (int32_t)(DAC_V_MAX / DAC_VREF * (float)DAC_MAX);
    if (next < lo || next > hi) {
      return cmd_err("trim ran out of DAC range");
    }
    dac_write((uint32_t)next);
  }
  return cmd_err("did not converge to 1 mV (last %.4f V)", (double)r.vbus);
}
