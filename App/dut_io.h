/* Named access to the DUT's control and debug pins (scan chain, TileLink, resets). */
#ifndef DUT_IO_H
#define DUT_IO_H

#include <stdbool.h>

void dut_reset_button(void);   /* EXTI context */
void dut_io_poll(void);

bool cmd_pin(int argc, char **argv);
bool cmd_pulse(int argc, char **argv);

#endif
