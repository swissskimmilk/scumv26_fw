#include "dut_uart.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "host_link.h"
#include "status.h"
#include "usart.h"

/* RX goes byte-by-byte from the interrupt into a single-producer/single-consumer ring:
 * the ISR only writes head, the main loop only writes tail, so no locking is needed. */
#define RX_SIZE 2048U /* power of two */

static uint8_t rx_buf[RX_SIZE];
static volatile uint32_t rx_head;
static volatile uint32_t rx_tail;
static volatile uint32_t rx_dropped;

void USART1_IRQHandler(void)
{
  uint32_t isr = USART1->ISR;
  while (isr & USART_ISR_RXNE_RXFNE) {
    uint8_t c = (uint8_t)USART1->RDR;
    uint32_t next = (rx_head + 1U) & (RX_SIZE - 1U);
    if (next != rx_tail) {
      rx_buf[rx_head] = c;
      rx_head = next;
    } else {
      rx_dropped++;
      status.uart_dropped++;
    }
    isr = USART1->ISR;
  }
  if (isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE)) {
    USART1->ICR = USART_ICR_ORECF | USART_ICR_FECF | USART_ICR_NECF;
  }
}

void dut_uart_init(void)
{
  SET_BIT(USART1->CR1, USART_CR1_RXNEIE_RXFNEIE);
  HAL_NVIC_SetPriority(USART1_IRQn, 3, 0);
  HAL_NVIC_EnableIRQ(USART1_IRQn);
}

static void set_baud(uint32_t baud)
{
  HAL_NVIC_DisableIRQ(USART1_IRQn);
  huart1.Init.BaudRate = baud;
  HAL_UART_Init(&huart1);
  dut_uart_init();
}

bool cmd_uart(int argc, char **argv)
{
  if (argc >= 2 && !strcmp(argv[1], "baud")) {
    uint32_t b;
    if (argc != 3 || !arg_u32(argv[2], &b) || b < 300 || b > 10000000U) {
      return cmd_err("usage: uart baud <300-10000000>");
    }
    set_baud(b);
    return true;
  }
  if (argc >= 3 && !strcmp(argv[1], "tx")) {
    /* Re-join the words the command parser split apart, then end the line. */
    for (int i = 2; i < argc; i++) {
      HAL_UART_Transmit(&huart1, (uint8_t *)argv[i], (uint16_t)strlen(argv[i]), 100);
      HAL_UART_Transmit(&huart1, (uint8_t *)(i + 1 < argc ? " " : "\r\n"),
                        i + 1 < argc ? 1 : 2, 100);
    }
    return true;
  }
  if (argc == 2 && !strcmp(argv[1], "rx")) {
    /* Printable bytes as-is, everything else (newlines too) as \xNN: the dump stays on
     * one line, so DUT output can never be mistaken for the final OK. */
    while (rx_tail != rx_head) {
      uint8_t c = rx_buf[rx_tail];
      rx_tail = (rx_tail + 1U) & (RX_SIZE - 1U);
      if (c >= 0x20 && c < 0x7F && c != '\\') {
        out("%c", c);
      } else {
        out("\\x%02X", c);
      }
    }
    out("\n");
    if (rx_dropped) {
      out("! %lu bytes dropped (buffer full)\n", rx_dropped);
      rx_dropped = 0;
    }
    return true;
  }
  return cmd_err("usage: uart baud <n> | uart tx <text> | uart rx");
}
