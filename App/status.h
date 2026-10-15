/* Latched event log: anything noteworthy is counted here, so a host that was not connected
 * (or missed an asynchronous "!" line) can still find out with `status`. */
#ifndef STATUS_H
#define STATUS_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint32_t ocp_trips;        /* over-current trips (all rails were dropped) */
  uint32_t ocp_rails;        /* bitmask of monitors that reported over-limit (mon_id_t) */
  uint32_t capture_errors;   /* DCMI overrun, DMA error or timeout */
  uint32_t uart_dropped;     /* DUT UART bytes lost to a full buffer */
  uint32_t host_dropped;     /* bytes of output thrown away while no host was connected */
  uint32_t button_resets;    /* DUT resets from the front-panel button */
} status_counters_t;

extern volatile status_counters_t status;

void status_init(void); /* records why the MCU last reset */

bool cmd_status(int argc, char **argv);

#endif
