/* Board-level pin helpers. Pin names come from the CubeMX labels in main.h. */
#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>
#include "main.h"

/* Two-level so aliases like LED_DUT expand to their CubeMX label before pasting. */
#define PIN_SET(name, on) PIN_SET_(name, on)
#define PIN_SET_(name, on) \
  HAL_GPIO_WritePin(name##_GPIO_Port, name##_Pin, (on) ? GPIO_PIN_SET : GPIO_PIN_RESET)
#define PIN_GET(name) PIN_GET_(name)
#define PIN_GET_(name) (HAL_GPIO_ReadPin(name##_GPIO_Port, name##_Pin) == GPIO_PIN_SET)
#define PIN_OUT_STATE(name) PIN_OUT_STATE_(name)
#define PIN_OUT_STATE_(name) ((name##_GPIO_Port->ODR & name##_Pin) != 0U)

/* Status LEDs drive low-side NMOS gates: high = lit. */
#define LED_HEARTBEAT LED1
#define LED_HOST      LED2
#define LED_DUT       LED3

static inline uint32_t millis(void) { return HAL_GetTick(); }

/* Busy-wait for short delays; DWT cycle counter is enabled in app_init(). */
static inline void delay_us(uint32_t us)
{
  uint32_t start = DWT->CYCCNT;
  uint32_t cycles = us * (SystemCoreClock / 1000000U);
  while ((DWT->CYCCNT - start) < cycles) {
  }
}

#endif
