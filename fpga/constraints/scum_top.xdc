# SCuMV26 bring-up board, XC7A100T-2FGG676I.
# Pins extracted from the Altium project (SCuM_CPGA_Breakout_FPGA, FPGA.SchDoc) on 2026-10-29.
# Banks 14/15 (FT2232H, flash, 100 MHz clock) are 3.3 V; 13 (DUT), 16 (ADC), 34 and 35 are 1.8 V.

set_property -dict {PACKAGE_PIN U20 IOSTANDARD LVCMOS18} [get_ports {BASEBAND_RX}]  ;# IO_L15N_T2_DQS_13
set_property -dict {PACKAGE_PIN T20 IOSTANDARD LVCMOS18} [get_ports {BASEBAND_TX}]  ;# IO_L15P_T2_DQS_13
set_property -dict {PACKAGE_PIN AB25 IOSTANDARD LVCMOS18} [get_ports {BOOT_SEL}]  ;# IO_L7N_T1_13
set_property -dict {PACKAGE_PIN W20 IOSTANDARD LVCMOS18} [get_ports {BTN_CHIP_RST}]  ;# IO_L16P_T2_13
set_property -dict {PACKAGE_PIN AC24 IOSTANDARD LVCMOS18} [get_ports {CLK_DEBUG_MUX}]  ;# IO_L9N_T1_DQS_13
set_property -dict {PACKAGE_PIN AB24 IOSTANDARD LVCMOS18} [get_ports {CORE_GPIO}]  ;# IO_L9P_T1_DQS_13
set_property -dict {PACKAGE_PIN AA24 IOSTANDARD LVCMOS18} [get_ports {CORE_RST}]  ;# IO_L7P_T1_13
set_property -dict {PACKAGE_PIN AA23 IOSTANDARD LVCMOS18} [get_ports {FPGA_UART_RX}]  ;# IO_L8N_T1_13
set_property -dict {PACKAGE_PIN AA22 IOSTANDARD LVCMOS18} [get_ports {FPGA_UART_TX}]  ;# IO_L8P_T1_13
set_property -dict {PACKAGE_PIN V23 IOSTANDARD LVCMOS18} [get_ports {NFC_DEMOD_MCU}]  ;# IO_L10P_T1_13
set_property -dict {PACKAGE_PIN W23 IOSTANDARD LVCMOS18} [get_ports {NFC_MDM_DBG}]  ;# IO_L10N_T1_13
set_property -dict {PACKAGE_PIN AB26 IOSTANDARD LVCMOS18} [get_ports {RDY_IN}]  ;# IO_L3P_T0_DQS_13
set_property -dict {PACKAGE_PIN AC26 IOSTANDARD LVCMOS18} [get_ports {RDY_OUT}]  ;# IO_L3N_T0_DQS_13
set_property -dict {PACKAGE_PIN W25 IOSTANDARD LVCMOS18} [get_ports {SCAN_CLK}]  ;# IO_L4P_T0_13
set_property -dict {PACKAGE_PIN Y26 IOSTANDARD LVCMOS18} [get_ports {SCAN_EN}]  ;# IO_L4N_T0_13
set_property -dict {PACKAGE_PIN Y25 IOSTANDARD LVCMOS18} [get_ports {SCAN_IN}]  ;# IO_L5P_T0_13
set_property -dict {PACKAGE_PIN AA25 IOSTANDARD LVCMOS18} [get_ports {SCAN_OUT}]  ;# IO_L5N_T0_13
set_property -dict {PACKAGE_PIN V24 IOSTANDARD LVCMOS18} [get_ports {SCAN_RST}]  ;# IO_L6P_T0_13
set_property -dict {PACKAGE_PIN W24 IOSTANDARD LVCMOS18} [get_ports {SCAN_SEL}]  ;# IO_L6N_T0_VREF_13
set_property -dict {PACKAGE_PIN U22 IOSTANDARD LVCMOS18} [get_ports {TL_CLK}]  ;# IO_L12P_T1_MRCC_13
set_property -dict {PACKAGE_PIN U25 IOSTANDARD LVCMOS18} [get_ports {TL_DAT_IN}]  ;# IO_L1P_T0_13
set_property -dict {PACKAGE_PIN U26 IOSTANDARD LVCMOS18} [get_ports {TL_DAT_OUT}]  ;# IO_L1N_T0_13
set_property -dict {PACKAGE_PIN V26 IOSTANDARD LVCMOS18} [get_ports {TL_VAL_IN}]  ;# IO_L2P_T0_13
set_property -dict {PACKAGE_PIN W26 IOSTANDARD LVCMOS18} [get_ports {TL_VAL_OUT}]  ;# IO_L2N_T0_13
set_property -dict {PACKAGE_PIN M21 IOSTANDARD LVCMOS33} [get_ports {CLK_100M}]  ;# IO_L12P_T1_MRCC_14
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {FT_D[0]}]  ;# IO_L4P_T0_D04_14
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports {FT_D[1]}]  ;# IO_L4N_T0_D05_14
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33} [get_ports {FT_D[2]}]  ;# IO_L5P_T0_D06_14
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports {FT_D[3]}]  ;# IO_L5N_T0_D07_14
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33} [get_ports {FT_D[4]}]  ;# IO_L6N_T0_D08_VREF_14
set_property -dict {PACKAGE_PIN K25 IOSTANDARD LVCMOS33} [get_ports {FT_D[5]}]  ;# IO_L7P_T1_D09_14
set_property -dict {PACKAGE_PIN K26 IOSTANDARD LVCMOS33} [get_ports {FT_D[6]}]  ;# IO_L7N_T1_D10_14
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33} [get_ports {FT_D[7]}]  ;# IO_L8P_T1_D11_14
set_property -dict {PACKAGE_PIN L25 IOSTANDARD LVCMOS33} [get_ports {FT_RD_N}]  ;# IO_L9N_T1_DQS_D13_14
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33} [get_ports {FT_RXF_N}]  ;# IO_L8N_T1_D12_14
set_property -dict {PACKAGE_PIN M25 IOSTANDARD LVCMOS33} [get_ports {FT_SIWU_N}]  ;# IO_L10N_T1_D15_14
set_property -dict {PACKAGE_PIN L24 IOSTANDARD LVCMOS33} [get_ports {FT_TXE_N}]  ;# IO_L9P_T1_DQS_14
set_property -dict {PACKAGE_PIN M24 IOSTANDARD LVCMOS33} [get_ports {FT_WR_N}]  ;# IO_L10P_T1_D14_14
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS18} [get_ports {ADC_CS}]  ;# IO_L6P_T0_16
set_property -dict {PACKAGE_PIN D18 IOSTANDARD LVCMOS18} [get_ports {ADC_DCLK}]  ;# IO_L12P_T1_MRCC_16
set_property -dict {PACKAGE_PIN H14 IOSTANDARD LVCMOS18} [get_ports {ADC_D[0]}]  ;# IO_L1P_T0_16
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS18} [get_ports {ADC_D[1]}]  ;# IO_L1N_T0_16
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS18} [get_ports {ADC_D[2]}]  ;# IO_L2P_T0_16
set_property -dict {PACKAGE_PIN F17 IOSTANDARD LVCMOS18} [get_ports {ADC_D[3]}]  ;# IO_L2N_T0_16
set_property -dict {PACKAGE_PIN F18 IOSTANDARD LVCMOS18} [get_ports {ADC_D[4]}]  ;# IO_L3P_T0_DQS_16
set_property -dict {PACKAGE_PIN F19 IOSTANDARD LVCMOS18} [get_ports {ADC_D[5]}]  ;# IO_L3N_T0_DQS_16
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS18} [get_ports {ADC_D[6]}]  ;# IO_L4P_T0_16
set_property -dict {PACKAGE_PIN F15 IOSTANDARD LVCMOS18} [get_ports {ADC_D[7]}]  ;# IO_L4N_T0_16
set_property -dict {PACKAGE_PIN G19 IOSTANDARD LVCMOS18} [get_ports {ADC_D[8]}]  ;# IO_L5P_T0_16
set_property -dict {PACKAGE_PIN F20 IOSTANDARD LVCMOS18} [get_ports {ADC_D[9]}]  ;# IO_L5N_T0_16
set_property -dict {PACKAGE_PIN B17 IOSTANDARD LVCMOS18} [get_ports {ADC_RST}]  ;# IO_L7N_T1_16
set_property -dict {PACKAGE_PIN G16 IOSTANDARD LVCMOS18} [get_ports {ADC_SCLK}]  ;# IO_L6N_T0_VREF_16
set_property -dict {PACKAGE_PIN C17 IOSTANDARD LVCMOS18} [get_ports {ADC_SDIO}]  ;# IO_L7P_T1_16
set_property -dict {PACKAGE_PIN C18 IOSTANDARD LVCMOS18} [get_ports {ADC_nDCLK}]  ;# IO_L12N_T1_MRCC_16
set_property -dict {PACKAGE_PIN N2 IOSTANDARD LVDS_25} [get_ports {CLK_BUF_1_N}]  ;# IO_L12N_T1_MRCC_34
set_property -dict {PACKAGE_PIN N3 IOSTANDARD LVDS_25} [get_ports {CLK_BUF_1_P}]  ;# IO_L12P_T1_MRCC_34
set_property -dict {PACKAGE_PIN M6 IOSTANDARD LVCMOS18} [get_ports {LED[0]}]  ;# IO_L6P_T0_34
set_property -dict {PACKAGE_PIN M5 IOSTANDARD LVCMOS18} [get_ports {LED[1]}]  ;# IO_L6N_T0_VREF_34
set_property -dict {PACKAGE_PIN K1 IOSTANDARD LVCMOS18} [get_ports {LED[2]}]  ;# IO_L7P_T1_34
set_property -dict {PACKAGE_PIN N6 IOSTANDARD LVCMOS18} [get_ports {OSC_IN_SEL}]  ;# IO_L5N_T0_34
set_property -dict {PACKAGE_PIN N7 IOSTANDARD LVCMOS18} [get_ports {RF_VDD_SW}]  ;# IO_L5P_T0_34
set_property -dict {PACKAGE_PIN M7 IOSTANDARD LVCMOS18} [get_ports {TELEM_ALERT}]  ;# IO_L2P_T0_34
set_property -dict {PACKAGE_PIN K3 IOSTANDARD LVCMOS18} [get_ports {TELEM_SCL}]  ;# IO_L1P_T0_34
set_property -dict {PACKAGE_PIN J3 IOSTANDARD LVCMOS18} [get_ports {TELEM_SDA}]  ;# IO_L1N_T0_34
set_property -dict {PACKAGE_PIN L7 IOSTANDARD LVCMOS18} [get_ports {VBAT_EN}]  ;# IO_L2N_T0_34
set_property -dict {PACKAGE_PIN K5 IOSTANDARD LVCMOS18} [get_ports {VDDA_SW}]  ;# IO_L4N_T0_34
set_property -dict {PACKAGE_PIN L5 IOSTANDARD LVCMOS18} [get_ports {VDDD_SW}]  ;# IO_L4P_T0_34
set_property -dict {PACKAGE_PIN M4 IOSTANDARD LVCMOS18} [get_ports {VDD_EN}]  ;# IO_L3P_T0_DQS_34
set_property -dict {PACKAGE_PIN L4 IOSTANDARD LVCMOS18} [get_ports {VIO_EN}]  ;# IO_L3N_T0_DQS_34

# FT2232H RD#/WR# idle high through configuration as well
set_property PULLUP TRUE [get_ports {FT_RD_N FT_WR_N}]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]

create_clock -period 10.000 -name sys_clk [get_ports CLK_100M]
# The DUT clock's rate is whatever the DUT makes; constrain the counter for the fastest we
# expect to count (500 MHz) so the 32-bit increment is checked at that rate.
create_clock -period 2.000 -name dut_clk [get_ports CLK_BUF_1_P]
set_clock_groups -asynchronous -group sys_clk -group dut_clk
# Gray-coded counter crossing: bound the skew between its bits to one DUT clock period.
set_max_delay -datapath_only 2.000 -from [get_cells -hier -filter {NAME =~ *u_clk/gray_reg*}] \
    -to [get_cells -hier -filter {NAME =~ *u_clk/g0_reg*}]

# Every other pin is either asynchronous (synchronised inside) or a handshake whose strobes
# are tens of ns wide (FT2232H async FIFO), so there is no I/O timing to close.
set_false_path -from [get_ports -filter {DIRECTION != OUT && NAME !~ CLK_*}]
set_false_path -to [get_ports -filter {DIRECTION != IN}]
