/* DUT power: regulator enables, sequencing, rail-source switches and the VBAT trim DAC. */
#ifndef POWER_H
#define POWER_H

#include <stdbool.h>

void power_init(void);
void power_poll(void);

/* Interrupt-safe: drops every DUT regulator enable at once (order: 1V, VBAT, VIO). */
void power_emergency_off(void);

bool power_vbat_set(float volts);  /* open-loop through the DAC */

bool cmd_power(int argc, char **argv);
bool cmd_rail(int argc, char **argv);
bool cmd_sw(int argc, char **argv);
bool cmd_vbat(int argc, char **argv);
bool cmd_trim(int argc, char **argv);
bool cmd_dac(int argc, char **argv);

#endif
