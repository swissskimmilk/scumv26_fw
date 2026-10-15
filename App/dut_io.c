#include "dut_io.h"

#include <string.h>

#include "board.h"
#include "cmd.h"
#include "host_link.h"
#include "status.h"

/* Pulse width for the front-panel DUT reset button, and its debounce hold-off. */
#define BTN_RESET_US 1000U
#define BTN_HOLDOFF_MS 250U

typedef struct {
  const char *name;
  GPIO_TypeDef *port;
  uint16_t pin;
  bool output; /* false = the DUT drives it */
} dut_pin_t;

#define OUT(n) {#n, n##_GPIO_Port, n##_Pin, true}
#define IN(n)  {#n, n##_GPIO_Port, n##_Pin, false}

/* CORE_RST polarity is not confirmed yet (both pull footprints exist); `pulse` drives the
 * opposite of the pin's present level, so it works whichever way it turns out. */
static const dut_pin_t pins[] = {
    OUT(CORE_RST), OUT(BOOT_SEL), IN(CORE_GPIO), OUT(CLK_DEBUG_MUX),
    OUT(SCAN_CLK), OUT(SCAN_IN), OUT(SCAN_EN), OUT(SCAN_SEL), OUT(SCAN_RST), IN(SCAN_OUT),
    OUT(TL_CLK), OUT(TL_VAL_IN), OUT(TL_DAT_IN), OUT(RDY_OUT),
    IN(TL_VAL_OUT), IN(TL_DAT_OUT), IN(RDY_IN),
    IN(BASEBAND_TX), IN(BASEBAND_RX), IN(NFC_DEMOD_MCU), IN(NFC_MDM_DBG),
};

#define N_PINS (sizeof pins / sizeof pins[0])

static volatile bool button_pressed;

static const dut_pin_t *find(const char *name)
{
  for (uint32_t i = 0; i < N_PINS; i++) {
    if (strcasecmp(name, pins[i].name) == 0) {
      return &pins[i];
    }
  }
  return NULL;
}

static bool level(const dut_pin_t *p)
{
  if (p->output) {
    return (p->port->ODR & p->pin) != 0U;
  }
  return HAL_GPIO_ReadPin(p->port, p->pin) == GPIO_PIN_SET;
}

void dut_reset_button(void)
{
  button_pressed = true;
}

void dut_io_poll(void)
{
  static uint32_t last_press;
  if (!button_pressed) {
    return;
  }
  button_pressed = false;
  if (millis() - last_press < BTN_HOLDOFF_MS) {
    return; /* contact bounce */
  }
  last_press = millis();
  const dut_pin_t *p = find("CORE_RST");
  bool idle = level(p);
  HAL_GPIO_WritePin(p->port, p->pin, idle ? GPIO_PIN_RESET : GPIO_PIN_SET);
  delay_us(BTN_RESET_US);
  HAL_GPIO_WritePin(p->port, p->pin, idle ? GPIO_PIN_SET : GPIO_PIN_RESET);
  status.button_resets++;
  out("! DUT reset (button)\n");
}

bool cmd_pin(int argc, char **argv)
{
  if (argc == 1) {
    for (uint32_t i = 0; i < N_PINS; i++) {
      out("%-14s %s %d\n", pins[i].name, pins[i].output ? "out" : "in ", level(&pins[i]));
    }
    return true;
  }
  const dut_pin_t *p = find(argv[1]);
  if (p == NULL) {
    return cmd_err("unknown pin '%s' (pin lists them)", argv[1]);
  }
  if (argc == 2) {
    out("%d\n", level(p));
    return true;
  }
  bool on;
  if (argc != 3 || !arg_onoff(argv[2], &on)) {
    return cmd_err("usage: pin <name> [0|1]");
  }
  if (!p->output) {
    return cmd_err("%s is driven by the DUT", p->name);
  }
  HAL_GPIO_WritePin(p->port, p->pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
  return true;
}

bool cmd_pulse(int argc, char **argv)
{
  uint32_t us;
  const dut_pin_t *p = argc == 3 ? find(argv[1]) : NULL;
  if (p == NULL || !p->output || !arg_u32(argv[2], &us) || us == 0 || us > 1000000U) {
    return cmd_err("usage: pulse <output pin> <1-1000000 us>");
  }
  bool idle = level(p);
  HAL_GPIO_WritePin(p->port, p->pin, idle ? GPIO_PIN_RESET : GPIO_PIN_SET);
  delay_us(us);
  HAL_GPIO_WritePin(p->port, p->pin, idle ? GPIO_PIN_SET : GPIO_PIN_RESET);
  return true;
}
