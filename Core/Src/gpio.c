/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    gpio.c
  * @brief   This file provides code for the configuration
  *          of all used GPIO pins.
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

/* Includes ------------------------------------------------------------------*/
#include "gpio.h"

/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/*----------------------------------------------------------------------------*/
/* Configure GPIO                                                             */
/*----------------------------------------------------------------------------*/
/* USER CODE BEGIN 1 */

/* USER CODE END 1 */

/** Configure pins
     PH0-OSC_IN (PH0)   ------> RCC_OSC_IN
     PH1-OSC_OUT (PH1)   ------> RCC_OSC_OUT
     PA13 (JTMS/SWDIO)   ------> DEBUG_JTMS-SWDIO
     PA14 (JTCK/SWCLK)   ------> DEBUG_JTCK-SWCLK
     PB3 (JTDO/TRACESWO)   ------> DEBUG_JTDO-SWO
*/
void MX_GPIO_Init(void)
{

  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOC, RF_VDD_SW_Pin|VDDD_SW_Pin|VDDA_SW_Pin|SCAN_CLK_Pin
                          |SCAN_IN_Pin|SCAN_EN_Pin|SCAN_SEL_Pin|SCAN_RST_Pin
                          |CLK_DEBUG_MUX_Pin|LED2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(TL_CLK_GPIO_Port, TL_CLK_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, TL_VAL_IN_Pin|VDD_EN_Pin|VBAT_EN_Pin|TL_DAT_IN_Pin
                          |RDY_OUT_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOE, CORE_RST_Pin|ADC_SEN_Pin, GPIO_PIN_SET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOE, BOOT_SEL_Pin|LED1_Pin|LED3_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOD, ADC_RESET_Pin|VIO_EN_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOD, ADC_VSYNC_DRV_Pin|ADC_HSYNC_DRV_Pin, GPIO_PIN_SET);

  /*Configure GPIO pins : RF_VDD_SW_Pin VDDD_SW_Pin VDDA_SW_Pin SCAN_CLK_Pin
                           SCAN_IN_Pin SCAN_EN_Pin SCAN_SEL_Pin SCAN_RST_Pin
                           CLK_DEBUG_MUX_Pin LED2_Pin */
  GPIO_InitStruct.Pin = RF_VDD_SW_Pin|VDDD_SW_Pin|VDDA_SW_Pin|SCAN_CLK_Pin
                          |SCAN_IN_Pin|SCAN_EN_Pin|SCAN_SEL_Pin|SCAN_RST_Pin
                          |CLK_DEBUG_MUX_Pin|LED2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /*Configure GPIO pins : SCAN_OUT_Pin CORE_GPIO_Pin */
  GPIO_InitStruct.Pin = SCAN_OUT_Pin|CORE_GPIO_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /*Configure GPIO pin : TELEM_ALERT_Pin */
  GPIO_InitStruct.Pin = TELEM_ALERT_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(TELEM_ALERT_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : OSC_IN_SEL_Pin NFC_MDM_DBG_Pin */
  GPIO_InitStruct.Pin = OSC_IN_SEL_Pin|NFC_MDM_DBG_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pin : TL_CLK_Pin */
  GPIO_InitStruct.Pin = TL_CLK_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(TL_CLK_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : TL_VAL_OUT_Pin RDY_IN_Pin NFC_DEMOD_MCU_Pin TL_DAT_OUT_Pin
                           BASEBAND_TX_Pin BASEBAND_RX_Pin */
  GPIO_InitStruct.Pin = TL_VAL_OUT_Pin|RDY_IN_Pin|NFC_DEMOD_MCU_Pin|TL_DAT_OUT_Pin
                          |BASEBAND_TX_Pin|BASEBAND_RX_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pins : TL_VAL_IN_Pin TL_DAT_IN_Pin RDY_OUT_Pin */
  GPIO_InitStruct.Pin = TL_VAL_IN_Pin|TL_DAT_IN_Pin|RDY_OUT_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pins : CORE_RST_Pin BOOT_SEL_Pin LED1_Pin LED3_Pin
                           ADC_SEN_Pin */
  GPIO_InitStruct.Pin = CORE_RST_Pin|BOOT_SEL_Pin|LED1_Pin|LED3_Pin
                          |ADC_SEN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

  /*Configure GPIO pin : BTN_DUT_RST_Pin */
  GPIO_InitStruct.Pin = BTN_DUT_RST_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(BTN_DUT_RST_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : VDD_EN_Pin VBAT_EN_Pin */
  GPIO_InitStruct.Pin = VDD_EN_Pin|VBAT_EN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pins : ADC_RESET_Pin VIO_EN_Pin */
  GPIO_InitStruct.Pin = ADC_RESET_Pin|VIO_EN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  /*Configure GPIO pins : ADC_VSYNC_DRV_Pin ADC_HSYNC_DRV_Pin */
  GPIO_InitStruct.Pin = ADC_VSYNC_DRV_Pin|ADC_HSYNC_DRV_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  /* EXTI interrupt init*/
  HAL_NVIC_SetPriority(EXTI1_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(EXTI1_IRQn);

  HAL_NVIC_SetPriority(EXTI11_IRQn, 7, 0);
  HAL_NVIC_EnableIRQ(EXTI11_IRQn);

}

/* USER CODE BEGIN 2 */

/* USER CODE END 2 */
