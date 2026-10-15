/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32u5xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define ADC_D6_Pin GPIO_PIN_4
#define ADC_D6_GPIO_Port GPIOE
#define ADC_D8_Pin GPIO_PIN_5
#define ADC_D8_GPIO_Port GPIOE
#define ADC_D9_Pin GPIO_PIN_6
#define ADC_D9_GPIO_Port GPIOE
#define RF_VDD_SW_Pin GPIO_PIN_13
#define RF_VDD_SW_GPIO_Port GPIOC
#define VDDD_SW_Pin GPIO_PIN_14
#define VDDD_SW_GPIO_Port GPIOC
#define VDDA_SW_Pin GPIO_PIN_15
#define VDDA_SW_GPIO_Port GPIOC
#define SCAN_CLK_Pin GPIO_PIN_0
#define SCAN_CLK_GPIO_Port GPIOC
#define SCAN_IN_Pin GPIO_PIN_1
#define SCAN_IN_GPIO_Port GPIOC
#define SCAN_OUT_Pin GPIO_PIN_2
#define SCAN_OUT_GPIO_Port GPIOC
#define SCAN_EN_Pin GPIO_PIN_3
#define SCAN_EN_GPIO_Port GPIOC
#define CLK_BUF_MCU_Pin GPIO_PIN_0
#define CLK_BUF_MCU_GPIO_Port GPIOA
#define TELEM_ALERT_Pin GPIO_PIN_1
#define TELEM_ALERT_GPIO_Port GPIOA
#define TELEM_ALERT_EXTI_IRQn EXTI1_IRQn
#define OSC_IN_SEL_Pin GPIO_PIN_3
#define OSC_IN_SEL_GPIO_Port GPIOA
#define DAC_VBAT_TRIM_Pin GPIO_PIN_4
#define DAC_VBAT_TRIM_GPIO_Port GPIOA
#define TL_CLK_Pin GPIO_PIN_5
#define TL_CLK_GPIO_Port GPIOA
#define NFC_MDM_DBG_Pin GPIO_PIN_7
#define NFC_MDM_DBG_GPIO_Port GPIOA
#define SCAN_SEL_Pin GPIO_PIN_4
#define SCAN_SEL_GPIO_Port GPIOC
#define SCAN_RST_Pin GPIO_PIN_5
#define SCAN_RST_GPIO_Port GPIOC
#define TL_VAL_OUT_Pin GPIO_PIN_0
#define TL_VAL_OUT_GPIO_Port GPIOB
#define RDY_IN_Pin GPIO_PIN_1
#define RDY_IN_GPIO_Port GPIOB
#define TL_VAL_IN_Pin GPIO_PIN_2
#define TL_VAL_IN_GPIO_Port GPIOB
#define CORE_RST_Pin GPIO_PIN_7
#define CORE_RST_GPIO_Port GPIOE
#define BOOT_SEL_Pin GPIO_PIN_8
#define BOOT_SEL_GPIO_Port GPIOE
#define LED1_Pin GPIO_PIN_9
#define LED1_GPIO_Port GPIOE
#define LED3_Pin GPIO_PIN_10
#define LED3_GPIO_Port GPIOE
#define BTN_DUT_RST_Pin GPIO_PIN_11
#define BTN_DUT_RST_GPIO_Port GPIOE
#define BTN_DUT_RST_EXTI_IRQn EXTI11_IRQn
#define ADC_SEN_Pin GPIO_PIN_12
#define ADC_SEN_GPIO_Port GPIOE
#define ADC_SCLK_Pin GPIO_PIN_13
#define ADC_SCLK_GPIO_Port GPIOE
#define ADC_SDIO_Pin GPIO_PIN_15
#define ADC_SDIO_GPIO_Port GPIOE
#define NFC_DEMOD_MCU_Pin GPIO_PIN_10
#define NFC_DEMOD_MCU_GPIO_Port GPIOB
#define VDD_EN_Pin GPIO_PIN_12
#define VDD_EN_GPIO_Port GPIOB
#define TELEM_SCL_Pin GPIO_PIN_13
#define TELEM_SCL_GPIO_Port GPIOB
#define TELEM_SDA_Pin GPIO_PIN_14
#define TELEM_SDA_GPIO_Port GPIOB
#define VBAT_EN_Pin GPIO_PIN_15
#define VBAT_EN_GPIO_Port GPIOB
#define ADC_HSYNC_Pin GPIO_PIN_8
#define ADC_HSYNC_GPIO_Port GPIOD
#define ADC_DCLK_Pin GPIO_PIN_9
#define ADC_DCLK_GPIO_Port GPIOD
#define ADC_RESET_Pin GPIO_PIN_10
#define ADC_RESET_GPIO_Port GPIOD
#define ADC_VSYNC_DRV_Pin GPIO_PIN_11
#define ADC_VSYNC_DRV_GPIO_Port GPIOD
#define ADC_HSYNC_DRV_Pin GPIO_PIN_12
#define ADC_HSYNC_DRV_GPIO_Port GPIOD
#define ADC_D2_Pin GPIO_PIN_6
#define ADC_D2_GPIO_Port GPIOC
#define ADC_D3_Pin GPIO_PIN_7
#define ADC_D3_GPIO_Port GPIOC
#define CORE_GPIO_Pin GPIO_PIN_8
#define CORE_GPIO_GPIO_Port GPIOC
#define CLK_DEBUG_MUX_Pin GPIO_PIN_9
#define CLK_DEBUG_MUX_GPIO_Port GPIOC
#define DUT_UART_RX_Pin GPIO_PIN_9
#define DUT_UART_RX_GPIO_Port GPIOA
#define DUT_UART_TX_Pin GPIO_PIN_10
#define DUT_UART_TX_GPIO_Port GPIOA
#define ADC_D10_Pin GPIO_PIN_10
#define ADC_D10_GPIO_Port GPIOC
#define LED2_Pin GPIO_PIN_11
#define LED2_GPIO_Port GPIOC
#define ADC_D11_Pin GPIO_PIN_12
#define ADC_D11_GPIO_Port GPIOC
#define VIO_EN_Pin GPIO_PIN_2
#define VIO_EN_GPIO_Port GPIOD
#define ADC_D7_Pin GPIO_PIN_3
#define ADC_D7_GPIO_Port GPIOD
#define TL_DAT_IN_Pin GPIO_PIN_4
#define TL_DAT_IN_GPIO_Port GPIOB
#define TL_DAT_OUT_Pin GPIO_PIN_5
#define TL_DAT_OUT_GPIO_Port GPIOB
#define RDY_OUT_Pin GPIO_PIN_6
#define RDY_OUT_GPIO_Port GPIOB
#define ADC_VSYNC_Pin GPIO_PIN_7
#define ADC_VSYNC_GPIO_Port GPIOB
#define BASEBAND_TX_Pin GPIO_PIN_8
#define BASEBAND_TX_GPIO_Port GPIOB
#define BASEBAND_RX_Pin GPIO_PIN_9
#define BASEBAND_RX_GPIO_Port GPIOB
#define ADC_D4_Pin GPIO_PIN_0
#define ADC_D4_GPIO_Port GPIOE
#define ADC_D5_Pin GPIO_PIN_1
#define ADC_D5_GPIO_Port GPIOE

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
