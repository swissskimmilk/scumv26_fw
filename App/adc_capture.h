/* ADC3910D065 I/Q capture: SPI control, DCMI + GPDMA snapshot into SRAM, dump to host. */
#ifndef ADC_CAPTURE_H
#define ADC_CAPTURE_H

#include <stdbool.h>

void adc_capture_init(void);

bool cmd_adc(int argc, char **argv);
bool cmd_cap(int argc, char **argv);

#endif
