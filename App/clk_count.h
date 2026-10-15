/* DUT clock frequency counter: LMK1D1204 -> SN65LVDS2 -> PA0 (TIM2_ETR). */
#ifndef CLK_COUNT_H
#define CLK_COUNT_H

#include <stdbool.h>

void clk_count_init(void);

bool cmd_clk(int argc, char **argv);
bool cmd_freq(int argc, char **argv);

#endif
