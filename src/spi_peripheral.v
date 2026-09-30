/*
 * SPI Peripheral Module
 */

`default_nettype none
 
module spi_peripheral (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       sclk,
    input  wire       copi,
    input  wire       ncs,
    output reg  [7:0] en_reg_out_7_0,
    output reg  [7:0] en_reg_out_15_8,
    output reg  [7:0] en_reg_pwm_7_0,
    output reg  [7:0] en_reg_pwm_15_8,
    output reg  [7:0] pwm_duty_cycle
);
 
  // ------------------------------------------------------------------------
  // 1. Signal Synchronization (2 FF Chain)
  // ------------------------------------------------------------------------
  reg [1:0] sclk_sync_chain;
  reg [1:0] copi_sync_chain;
  reg [1:0] ncs_sync_chain;
 
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sclk_sync_chain <= 2'b11;
      copi_sync_chain <= 2'b00;
      ncs_sync_chain  <= 2'b11;
    end else begin
      sclk_sync_chain <= {sclk_sync_chain[0], sclk};
      copi_sync_chain <= {copi_sync_chain[0], copi};
      ncs_sync_chain  <= {ncs_sync_chain[0], ncs};
    end
  end
 
  wire sclk_sync = sclk_sync_chain[1];
  wire copi_sync = copi_sync_chain[1];
  wire ncs_sync  = ncs_sync_chain[1];
 
  // ------------------------------------------------------------------------
  // 2. Edge Detection
  // ------------------------------------------------------------------------
  reg sclk_prev;
  reg ncs_prev;
 
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sclk_prev <= 1'b1;
      ncs_prev  <= 1'b1;
    end else begin
      sclk_prev <= sclk_sync;
      ncs_prev  <= ncs_sync;
    end
  end
 
  wire sclk_rising = (sclk_sync == 1'b1) && (sclk_prev == 1'b0);
  wire ncs_rising  = (ncs_sync == 1'b1)  && (ncs_prev == 1'b0);
 
  // ------------------------------------------------------------------------
  // 3. Shift Register & Bit Counting
  // ------------------------------------------------------------------------
  reg [15:0] shift_reg;
  reg [4:0]  bit_count;
 
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      shift_reg <= 16'h0000;
      bit_count <= 5'd0;
    end else if (!ncs_sync) begin
      if (sclk_rising) begin
        shift_reg <= {shift_reg[14:0], copi_sync};
        bit_count <= bit_count + 1'b1;
      end
    end else begin
      bit_count <= 5'd0; // Reset counter when CS is high
    end
  end
 
  // ------------------------------------------------------------------------
  // 4. Register Updates on CS Rising Edge
  // ------------------------------------------------------------------------
  wire       rw   = shift_reg[15];    // 1 = write, 0 = read (reads are ignored)
  wire [6:0] addr = shift_reg[14:8];
  wire [7:0] data = shift_reg[7:0];
 
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en_reg_out_7_0  <= 8'h00;
      en_reg_out_15_8 <= 8'h00;
      en_reg_pwm_7_0  <= 8'h00;
      en_reg_pwm_15_8 <= 8'h00;
      pwm_duty_cycle  <= 8'h00;
    end else if (ncs_rising && (bit_count == 5'd16) && rw) begin
      case (addr)
        7'h00: en_reg_out_7_0  <= data;
        7'h01: en_reg_out_15_8 <= data;
        7'h02: en_reg_pwm_7_0  <= data;
        7'h03: en_reg_pwm_15_8 <= data;
        7'h04: pwm_duty_cycle  <= data;
        default: ; // Ignore invalid addresses
      endcase
    end
  end
 
endmodule