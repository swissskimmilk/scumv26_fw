/* INA237 rail monitors and the TLA2528 DUT analog ADC, all on I2C2 (TELEM bus). */
#ifndef TELEMETRY_H
#define TELEMETRY_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  MON_VIO,
  MON_VBAT,
  MON_VBAT_RF,
  MON_VDDD,
  MON_VDDA,
  MON_RF_VDD,
  MON_COUNT
} mon_id_t;

typedef struct {
  float vbus;    /* V, measured on the DUT side of the shunt */
  float vshunt;  /* V */
  float current; /* A, vshunt / R_shunt */
} mon_reading_t;

void telemetry_init(void);
void telemetry_poll(void);

bool mon_read(mon_id_t id, mon_reading_t *r);
int mon_find(const char *name);     /* -1 if unknown */
const char *mon_name(mon_id_t id);

bool cmd_tele(int argc, char **argv);
bool cmd_ilim(int argc, char **argv);
bool cmd_aux(int argc, char **argv);
bool cmd_i2c(int argc, char **argv);

#endif
