#include "app.h"

#include "adc_capture.h"
#include "board.h"
#include "clk_count.h"
#include "dut_io.h"
#include "dut_uart.h"
#include "host_link.h"
#include "power.h"
#include "status.h"
#include "telemetry.h"

#define HEARTBEAT_MS 500U

void app_init(void)
{
  /* Cycle counter for delay_us(). */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  status_init();
  power_init();
  telemetry_init();
  clk_count_init();
  adc_capture_init();
  dut_uart_init();
  host_link_init();
}

/* Superloop: interrupts and DMA do the time-critical work and leave flags; everything
 * here runs to completion quickly and in order. */
void app_loop(void)
{
  uint32_t last_beat = 0;
  for (;;) {
    host_link_poll(); /* USB stack, then any complete command line */
    telemetry_poll(); /* report over-current trips */
    power_poll();
    dut_io_poll();

    if (millis() - last_beat >= HEARTBEAT_MS) {
      last_beat = millis();
      HAL_GPIO_TogglePin(LED1_GPIO_Port, LED1_Pin);
    }
  }
}
