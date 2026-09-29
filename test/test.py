import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from cocotb.types import LogicArray
 
CLK_PERIOD_NS = 100                  # 10 MHz system clock
MAX_WAIT_CYCLES = 100_000            # 10 ms of sim time at 10 MHz
PWM_PERIOD_CYCLES_NOMINAL = 13 * 256 # (clk_div_trig + 1) * 256 = 3328 cycles -> ~3004.8 Hz
 
 
def ui_in_logicarray(ncs, bit, sclk):
    """Map UI inputs: [ui_in[7:3]=00000, ui_in[2]=nCS, ui_in[1]=COPI, ui_in[0]=SCLK]."""
    return LogicArray(f"00000{ncs}{bit}{sclk}")
 
 
def read_out0(dut):
    """
    Return uo_out[0] as '0' or '1' (raises on X/Z).
 
    NOTE: we deliberately read uo_out itself and take bit 0. tb.v's `uo_out_0`
    is declared as `wire [7:0]`, i.e. an 8-bit vector, and cocotb's
    RisingEdge/FallingEdge only fire on values that are exactly "1"/"0" for
    single-bit signals. On an 8-bit vector ("00000001") they never trigger,
    which is what caused the SimTimeoutError.
    """
    s = str(dut.uo_out.value)
    b = s[-1]
    assert b in "01", f"uo_out[0] is not a clean 0/1 (got '{b}', full uo_out={s})"
    return b
 
 
async def send_spi_transaction(dut, r_w, address, data):
    """
    Sends a 16-bit SPI Mode 0 transaction.
    All pin changes are aligned to falling edges of the system clock so they
    never land near a rising edge (avoids setup/hold problems in GL sims).
    """
    data_int = int(data) if isinstance(data, LogicArray) else data
    frame = (int(r_w) << 15) | ((address & 0x7F) << 8) | (data_int & 0xFF)
 
    # 1. Assert nCS (low) with SCLK idle low
    ncs, sclk, bit = 0, 0, 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 20, rising=False)
 
    # 2. 16 bits, MSB first
    for i in range(16):
        bit = (frame >> (15 - i)) & 0x1
 
        sclk = 0  # set up data while SCLK low
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await ClockCycles(dut.clk, 50, rising=False)
 
        sclk = 1  # DUT samples on this rising SCLK edge
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await ClockCycles(dut.clk, 50, rising=False)
 
    # 3. SCLK back to idle low
    sclk = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 20, rising=False)
 
    # 4. De-assert nCS -> DUT commits the write on this rising edge
    ncs = 1
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
 
    # 5. Let the synchronizers see nCS rise and the register update settle
    await ClockCycles(dut.clk, 100, rising=False)
 
 
async def start_clock_and_reset(dut):
    """Start the clock and reset the DUT with all inputs at known values."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units="ns").start())
 
    dut._log.info("Resetting DUT")
    # Known values from time 0 (no X on inputs while reset is asserted)
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.ui_in.value = ui_in_logicarray(ncs=1, bit=0, sclk=0)
    dut.rst_n.value = 0
 
    await ClockCycles(dut.clk, 20, rising=False)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 20, rising=False)
 
 
async def wait_for_out0_edge(dut, rising, max_cycles=MAX_WAIT_CYCLES):
    """
    Sample uo_out[0] on every falling clock edge and return the number of
    clock cycles until the requested edge is seen.
    """
    prev = read_out0(dut)
    for cycles in range(1, max_cycles + 1):
        await FallingEdge(dut.clk)
        cur = read_out0(dut)
        if rising and prev == "0" and cur == "1":
            return cycles
        if (not rising) and prev == "1" and cur == "0":
            return cycles
        prev = cur
    raise AssertionError(
        f"Timed out after {max_cycles} cycles waiting for a "
        f"{'rising' if rising else 'falling'} edge on uo_out[0] (last value: {prev})"
    )
 
 
@cocotb.test()
async def test_pwm_freq(dut):
    dut._log.info("Start PWM Frequency Test")
    await start_clock_and_reset(dut)
 
    # Output enable (0x00), PWM mode (0x02), duty cycle 50% (0x04)
    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x04, data=0x80)
 
    # Period = cycles between two consecutive rising edges
    await wait_for_out0_edge(dut, rising=True)
    period_cycles = await wait_for_out0_edge(dut, rising=True)
 
    period_ns = period_cycles * CLK_PERIOD_NS
    freq_hz = 1e9 / period_ns
    dut._log.info(f"Measured PWM period: {period_ns} ns ({freq_hz:.2f} Hz)")
 
    assert 2970 <= freq_hz <= 3030, f"Expected 3000 Hz +/-1%, got {freq_hz:.2f} Hz"
 
 
@cocotb.test()
async def test_pwm_duty(dut):
    dut._log.info("Start PWM Duty Cycle Test")
    await start_clock_and_reset(dut)
 
    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)
 
    test_cases = [
        (0x00, 0.0),     # 0%   -> always low
        (0x80, 50.0),    # 50%
        (0xFF, 100.0),   # 100% -> always high
    ]
 
    for data_val, expected_pct in test_cases:
        dut._log.info(f"Testing duty register 0x04 = 0x{data_val:02X} (~{expected_pct}%)")
        await send_spi_transaction(dut, r_w=1, address=0x04, data=data_val)
 
        # Let the PWM pick up the new value
        await ClockCycles(dut.clk, 100)
 
        if data_val in (0x00, 0xFF):
            expected = "0" if data_val == 0x00 else "1"
            # Watch for more than one full PWM period (3328 cycles)
            for _ in range(PWM_PERIOD_CYCLES_NOMINAL + 200):
                await FallingEdge(dut.clk)
                assert read_out0(dut) == expected, (
                    f"Expected output to stay {expected} at duty 0x{data_val:02X}"
                )
        else:
            # rising -> falling gives high time; rising -> next rising gives period
            await wait_for_out0_edge(dut, rising=True)
            high_cycles = await wait_for_out0_edge(dut, rising=False)
            low_cycles = await wait_for_out0_edge(dut, rising=True)
            period_cycles = high_cycles + low_cycles
 
            measured_duty = high_cycles / period_cycles * 100.0
            dut._log.info(f"Measured duty cycle: {measured_duty:.2f}%")
            assert abs(measured_duty - expected_pct) <= 1.0, (
                f"Duty cycle error: expected {expected_pct}%, got {measured_duty:.2f}%"
            )