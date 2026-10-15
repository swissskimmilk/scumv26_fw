/* DUT UART bridge on USART1 (PA9 -> DUT RX, PA10 <- DUT TX). */
#ifndef DUT_UART_H
#define DUT_UART_H

#include <stdbool.h>

void dut_uart_init(void);

bool cmd_uart(int argc, char **argv);

#endif
