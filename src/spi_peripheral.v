/*
 * SPI Peripheral Module
 */

`default_nettype none

module spi_peripheral (
    input  wire clk,
    input  wire rst_n,
    input  wire sclk,
    input  wire copi,
    input  wire ncs,
    output wire [7:0] en_reg_out_7_0,
    output wire [7:0] en_reg_out_15_8,
    output wire [7:0] en_reg_pwm_7_0,
    output wire [7:0] en_reg_pwm_15_8,
    output wire [7:0] pwm_duty_cycle
);

  // Implement your SPI receiver and register write logic here

endmodule