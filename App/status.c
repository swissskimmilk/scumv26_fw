#include "status.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "host_link.h"
#include "telemetry.h"

volatile status_counters_t status;
static char reset_cause[64];

void status_init(void)
{
  /* RCC->CSR keeps the reset-source flags until cleared; read them once at boot. */
  reset_cause[0] = '\0';
  static const struct {
    uint32_t flag;
    const char *name;
  } causes[] = {
      {RCC_FLAG_BORRST, "brownout"},   {RCC_FLAG_PINRST, "pin"},
      {RCC_FLAG_SFTRST, "software"},   {RCC_FLAG_IWDGRST, "watchdog"},
      {RCC_FLAG_WWDGRST, "window-wdg"}, {RCC_FLAG_LPWRRST, "low-power"},
      {RCC_FLAG_OBLRST, "option-bytes"},
  };
  for (uint32_t i = 0; i < sizeof causes / sizeof causes[0]; i++) {
    if (__HAL_RCC_GET_FLAG(causes[i].flag)) {
      if (reset_cause[0] != '\0') {
        strcat(reset_cause, "+");
      }
      strcat(reset_cause, causes[i].name);
    }
  }
  if (reset_cause[0] == '\0') {
    strcpy(reset_cause, "unknown");
  }
  __HAL_RCC_CLEAR_RESET_FLAGS();
}

/* `status` prints and clears; `status peek` prints without clearing. */
bool cmd_status(int argc, char **argv)
{
  bool clear = !(argc == 2 && !strcmp(argv[1], "peek"));
  if (argc > 2 || (argc == 2 && clear)) {
    return cmd_err("usage: status [peek]");
  }

  __disable_irq();
  status_counters_t s = status;
  if (clear) {
    memset((void *)&status, 0, sizeof status);
  }
  __enable_irq();

  out("uptime_s %lu\n", (unsigned long)(millis() / 1000U));
  out("mcu_reset %s\n", reset_cause);
  out("rails vio=%d vbat=%d 1v=%d\n", PIN_OUT_STATE(VIO_EN), PIN_OUT_STATE(VBAT_EN),
      PIN_OUT_STATE(VDD_EN));
  out("ocp_trips %lu", (unsigned long)s.ocp_trips);
  for (int i = 0; i < MON_COUNT; i++) {
    if (s.ocp_rails & (1U << i)) {
      out(" %s", mon_name((mon_id_t)i));
    }
  }
  out("\n");
  out("capture_errors %lu\n", (unsigned long)s.capture_errors);
  out("uart_dropped %lu\n", (unsigned long)s.uart_dropped);
  out("host_dropped %lu\n", (unsigned long)s.host_dropped);
  out("button_resets %lu\n", (unsigned long)s.button_resets);
  return true;
}
