#include "cmd.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "adc_capture.h"
#include "board.h"
#include "clk_count.h"
#include "dut_io.h"
#include "dut_uart.h"
#include "host_link.h"
#include "power.h"
#include "status.h"
#include "telemetry.h"

#define FW_VERSION "0.1.0"
#define MAX_ARGS 32

static bool cmd_help(int argc, char **argv);

static bool cmd_id(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  out("scumv26_fw " FW_VERSION " " __DATE__ " " __TIME__ " uid %08lX%08lX%08lX\n",
      (unsigned long)HAL_GetUIDw0(), (unsigned long)HAL_GetUIDw1(),
      (unsigned long)HAL_GetUIDw2());
  return true;
}

static const struct {
  const char *name;
  cmd_fn fn;
  const char *help;
} commands[] = {
    {"help", cmd_help, "list commands"},
    {"id", cmd_id, "firmware version and MCU UID"},
    {"status", cmd_status, "status [peek]          event counters since last read (then cleared)"},
    {"power", cmd_power, "power up|down          sequenced DUT rails (VIO, VBAT, 1V)"},
    {"rail", cmd_rail, "rail [vio|vbat|1v on|off]   one regulator enable, or show all"},
    {"sw", cmd_sw, "sw [vddd|vdda|rf board|dut]   DUT rail source switches"},
    {"vbat", cmd_vbat, "vbat [volts]           set VBAT through the DAC trim (1.16-1.55)"},
    {"trim", cmd_trim, "trim <volts>           closed-loop VBAT using its INA237"},
    {"dac", cmd_dac, "dac [code]             raw 12-bit DAC code on the VBAT trim"},
    {"tele", cmd_tele, "tele                   read all six INA237 rail monitors"},
    {"ilim", cmd_ilim, "ilim [rail mA]         over-current trip, 0 = off; a trip drops every rail"},
    {"aux", cmd_aux, "aux                    read the TLA2528 DUT analog channels"},
    {"i2c", cmd_i2c, "i2c scan               list responding I2C addresses"},
    {"clk", cmd_clk, "clk [sel dig|ana|off] [psc 1|2|4|8] [win ms]   DUT clock counter"},
    {"freq", cmd_freq, "freq                   latest DUT clock frequency"},
    {"adc", cmd_adc, "adc init|reset|rd a|wr a v|mode m|pat p   ADC3910 control"},
    {"cap", cmd_cap, "cap <words>            capture, then dump binary sample words"},
    {"uart", cmd_uart, "uart baud n|tx text|rx   DUT UART bridge"},
    {"pin", cmd_pin, "pin [name [0|1]]       read or drive a DUT control pin"},
    {"pulse", cmd_pulse, "pulse <name> <us>      pulse a DUT pin (e.g. CORE_RST)"},
};

#define N_COMMANDS (sizeof commands / sizeof commands[0])

static bool cmd_help(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  for (uint32_t i = 0; i < N_COMMANDS; i++) {
    out("  %-6s %s\n", commands[i].name, commands[i].help);
  }
  return true;
}

bool cmd_err(const char *fmt, ...)
{
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  out("ERR %s\n", buf);
  return false;
}

void cmd_execute(char *line)
{
  char *argv[MAX_ARGS];
  int argc = 0;
  for (char *tok = strtok(line, " \t"); tok; tok = strtok(NULL, " \t")) {
    if (argc == MAX_ARGS) {
      cmd_err("more than %d words", MAX_ARGS); /* never act on a silently cut-off line */
      return;
    }
    argv[argc++] = tok;
  }
  if (argc == 0) {
    return;
  }
  for (uint32_t i = 0; i < N_COMMANDS; i++) {
    if (strcmp(argv[0], commands[i].name) == 0) {
      if (commands[i].fn(argc, argv)) {
        out("OK\n");
      }
      return;
    }
  }
  cmd_err("unknown command '%s' (try help)", argv[0]);
}

bool arg_u32(const char *s, uint32_t *out_v)
{
  char *end;
  unsigned long v = strtoul(s, &end, 0);
  if (end == s || *end != '\0') {
    return false;
  }
  *out_v = (uint32_t)v;
  return true;
}

bool arg_float(const char *s, float *out_v)
{
  char *end;
  float v = strtof(s, &end);
  if (end == s || *end != '\0') {
    return false;
  }
  *out_v = v;
  return true;
}

bool arg_onoff(const char *s, bool *out_v)
{
  if (!strcmp(s, "on") || !strcmp(s, "1")) {
    *out_v = true;
  } else if (!strcmp(s, "off") || !strcmp(s, "0")) {
    *out_v = false;
  } else {
    return false;
  }
  return true;
}
