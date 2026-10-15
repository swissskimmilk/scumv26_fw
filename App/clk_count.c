#include "clk_count.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "host_link.h"
#include "tim.h"

/* TIM2 (32-bit) counts every edge on PA0 in hardware (external clock mode 2, through the
 * ETR prescaler). TIM6, clocked from the crystal-derived 160 MHz, interrupts once per
 * window and latches TIM2's count. The CPU never sees individual DUT clock edges.
 *
 * The latch happens in software, so its timing error is the jitter of a top-priority
 * ISR entry: a few cycles (tens of ns) per window, under 1 ppm for windows >= 100 ms
 * and well below the crystal's own tolerance.
 *
 * Limit: the prescaled ETR signal must stay below TIM2CLK / 4 = 40 MHz, so the input
 * can reach 40 MHz at psc 1, 80 MHz at psc 2 (marginal), 160 MHz at psc 4. */

#define TIM6_TICK_HZ 10000U

static uint32_t psc = 4;
static uint32_t window_ms = 100;

static volatile uint32_t last_count;
static volatile uint32_t latest_delta;
static volatile uint32_t sample_seq;
static volatile bool have_last;

static void apply_psc(void)
{
  uint32_t etps = psc == 1 ? TIM_ETRPRESCALER_DIV1
                : psc == 2 ? TIM_ETRPRESCALER_DIV2
                : psc == 4 ? TIM_ETRPRESCALER_DIV4
                           : TIM_ETRPRESCALER_DIV8;
  MODIFY_REG(TIM2->SMCR, TIM_SMCR_ETPS, etps);
}

static void restart_window(void)
{
  __disable_irq();
  have_last = false;
  sample_seq = 0;
  TIM6->ARR = window_ms * (TIM6_TICK_HZ / 1000U) - 1U;
  TIM6->CNT = 0;
  TIM6->EGR = TIM_EGR_UG; /* load ARR now */
  TIM6->SR = 0;
  __enable_irq();
}

void clk_count_init(void)
{
  apply_psc();
  HAL_TIM_Base_Start(&htim2);

  __HAL_RCC_TIM6_CLK_ENABLE();
  TIM6->PSC = HAL_RCC_GetPCLK1Freq() / TIM6_TICK_HZ - 1U; /* APB1 prescaler is 1 */
  TIM6->DIER = TIM_DIER_UIE;
  restart_window();
  HAL_NVIC_SetPriority(TIM6_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(TIM6_IRQn);
  TIM6->CR1 = TIM_CR1_CEN;
}

void TIM6_IRQHandler(void)
{
  uint32_t now = TIM2->CNT;
  TIM6->SR = ~TIM_SR_UIF;
  if (have_last) {
    latest_delta = now - last_count;
    sample_seq++;
  }
  last_count = now;
  have_last = true;
}

static const char *sel_name(void)
{
  uint32_t shift = POSITION_VAL(OSC_IN_SEL_Pin) * 2U;
  if (((OSC_IN_SEL_GPIO_Port->MODER >> shift) & 3U) != 1U) { /* not an output */
    return "off";
  }
  return PIN_OUT_STATE(OSC_IN_SEL) ? "ana" : "dig";
}

static void set_sel(const char *which)
{
  GPIO_InitTypeDef g = {.Pin = OSC_IN_SEL_Pin, .Pull = GPIO_NOPULL,
                        .Speed = GPIO_SPEED_FREQ_LOW};
  if (!strcmp(which, "off")) {
    g.Mode = GPIO_MODE_INPUT; /* LMK1D IN_SEL floating = all outputs disabled */
  } else {
    PIN_SET(OSC_IN_SEL, !strcmp(which, "ana")); /* 0 = IN0 (dig_out), 1 = IN1 (ana_out) */
    g.Mode = GPIO_MODE_OUTPUT_PP;
  }
  HAL_GPIO_Init(OSC_IN_SEL_GPIO_Port, &g);
  restart_window();
}

bool cmd_clk(int argc, char **argv)
{
  for (int i = 1; i + 1 < argc; i += 2) {
    uint32_t v;
    if (!strcmp(argv[i], "sel")) {
      if (strcmp(argv[i + 1], "dig") && strcmp(argv[i + 1], "ana") && strcmp(argv[i + 1], "off")) {
        return cmd_err("sel is dig, ana or off");
      }
      set_sel(argv[i + 1]);
    } else if (!strcmp(argv[i], "psc")) {
      if (!arg_u32(argv[i + 1], &v) || (v != 1 && v != 2 && v != 4 && v != 8)) {
        return cmd_err("psc is 1, 2, 4 or 8");
      }
      psc = v;
      apply_psc();
      restart_window();
    } else if (!strcmp(argv[i], "win")) {
      if (!arg_u32(argv[i + 1], &v) || v < 1 || v > 6000) {
        return cmd_err("win is 1-6000 ms");
      }
      window_ms = v;
      restart_window();
    } else {
      return cmd_err("unknown option '%s'", argv[i]);
    }
  }
  if (argc % 2 == 0) {
    return cmd_err("usage: clk [sel dig|ana|off] [psc 1|2|4|8] [win ms]");
  }
  out("sel %s  psc %lu  win %lu ms  resolution %.1f Hz\n", sel_name(), psc, window_ms,
      (double)psc * 1000.0 / (double)window_ms);
  return true;
}

bool cmd_freq(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  /* Wait for a fresh window so the answer reflects the current input. */
  uint32_t seq = sample_seq;
  uint32_t t0 = millis();
  while (sample_seq == seq) {
    host_link_service();
    if (millis() - t0 > 2U * window_ms + 50U) {
      return cmd_err("no window completed");
    }
  }
  uint32_t delta = latest_delta;
  double hz = (double)delta * (double)psc * 1000.0 / (double)window_ms;
  out("%.1f Hz  (%lu counts x%lu in %lu ms)\n", hz, delta, psc, window_ms);
  return true;
}
