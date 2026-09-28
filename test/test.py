import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, with_timeout
from cocotb.types import LogicArray

# 10 ms max timeout for waiting on an edge
EDGE_TIMEOUT_NS = 10_000_000  


def ui_in_logicarray(ncs, bit, sclk):
    """Map UI inputs: [ui_in[7:3]=00000, ui_in[2]=nCS, ui_in[1]=COPI, ui_in[0]=SCLK]."""
    return LogicArray(f"00000{ncs}{bit}{sclk}")


async def send_spi_transaction(dut, r_w, address, data):
    """
    Sends a 16-bit SPI Mode 0 Transaction.
    Pin transitions are aligned to the Falling Edge of the system clock 
    to prevent setup/hold violations in Gate-Level CDC synchronizers.
    """
    data_int = int(data) if isinstance(data, LogicArray) else data
    frame = (int(r_w) << 15) | ((address & 0x7F) << 8) | (data_int & 0xFF)

    # 1. Drive CS low with SCLK low (Idle state for Mode 0)
    sclk = 0
    ncs = 0
    bit = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    
    # Wait 20 cycles aligned to FALLING EDGES
    await ClockCycles(dut.clk, 20, rising=False)  

    # 2. Transmit 16 Bits (MSB first)
    for i in range(16):
        bit = (frame >> (15 - i)) & 0x1
        
        # Setup data bit while SCLK is LOW
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await ClockCycles(dut.clk, 50, rising=False)

        # Drive SCLK HIGH (DUT samples data on this rising edge)
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await ClockCycles(dut.clk, 50, rising=False)

    # 3. Return SCLK to idle LOW before de-asserting CS
    sclk = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 20, rising=False)

    # 4. End Transaction - Pull nCS HIGH
    ncs = 1
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)

    # 5. Allow CDC 2-FF synchronizers to detect nCS rising edge safely
    await ClockCycles(dut.clk, 100, rising=False)


async def reset_dut(dut):
    """Resets the DUT safely aligned to falling edges."""
    dut._log.info("Resetting DUT")

    if hasattr(dut, "VPWR"):
        dut.VPWR.value = 1
    if hasattr(dut, "VGND"):
        dut.VGND.value = 0

    dut.ena.value = 1
    dut.uio_in.value = 0
    
    # Drive reset changes on the falling edge to prevent reset recovery/removal X-states
    await ClockCycles(dut.clk, 1, rising=False)
    dut.ui_in.value = ui_in_logicarray(ncs=1, bit=0, sclk=0)
    dut.rst_n.value = 0
    
    await ClockCycles(dut.clk, 20, rising=False)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 20, rising=False)


@cocotb.test()
async def test_pwm_freq(dut):
    dut._log.info("Start PWM Frequency Test")

    # System clock: 10 MHz (100 ns period)
    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    # Enable Output (0x00), PWM Mode (0x02), and set Duty Cycle to 50% (0x04)
    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x04, data=0x80)

    # Measure period across two consecutive rising edges on uo_out[0]
    await with_timeout(RisingEdge(dut.uo_out[0]), EDGE_TIMEOUT_NS, "ns")
    t_start = cocotb.utils.get_sim_time(units="ns")

    await with_timeout(RisingEdge(dut.uo_out[0]), EDGE_TIMEOUT_NS, "ns")
    t_end = cocotb.utils.get_sim_time(units="ns")

    period_ns = t_end - t_start
    freq_hz = 1e9 / period_ns

    dut._log.info(f"Measured PWM Period: {period_ns} ns ({freq_hz:.2f} Hz)")

    # Verify tolerance: 3 kHz ±1% (2970 Hz to 3030 Hz)
    assert 2970 <= freq_hz <= 3030, f"Expected 3000 Hz ±1%, got {freq_hz:.2f} Hz"


@cocotb.test()
async def test_pwm_duty(dut):
    dut._log.info("Start PWM Duty Cycle Test")

    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    await send_spi_transaction(dut, r_w=1, address=0x00, data=0xFF)
    await send_spi_transaction(dut, r_w=1, address=0x02, data=0xFF)

    test_cases = [
        (0x00, 0.0),    # 0% Duty Cycle
        (0x80, 50.0),   # 50% Duty Cycle
        (0xFF, 100.0)   # 100% Duty Cycle
    ]

    for data_val, expected_pct in test_cases:
        dut._log.info(f"Testing Duty Cycle Reg 0x04 = 0x{data_val:02X} (~{expected_pct}%)")
        await send_spi_transaction(dut, r_w=1, address=0x04, data=data_val)

        # Allow PWM generator logic time to grab the new register values
        await ClockCycles(dut.clk, 100)

        if data_val == 0x00:
            for _ in range(3500):
                await ClockCycles(dut.clk, 1)
                assert dut.uo_out[0].value == 0, f"Expected 0% output to stay LOW, got {dut.uo_out[0].value}"

        elif data_val == 0xFF:
            for _ in range(3500):
                await ClockCycles(dut.clk, 1)
                assert dut.uo_out[0].value == 1, f"Expected 100% output to stay HIGH, got {dut.uo_out[0].value}"

        else:
            await with_timeout(RisingEdge(dut.uo_out[0]), EDGE_TIMEOUT_NS, "ns")
            t_rise = cocotb.utils.get_sim_time(units="ns")

            await with_timeout(FallingEdge(dut.uo_out[0]), EDGE_TIMEOUT_NS, "ns")
            t_fall = cocotb.utils.get_sim_time(units="ns")

            await with_timeout(RisingEdge(dut.uo_out[0]), EDGE_TIMEOUT_NS, "ns")
            t_next_rise = cocotb.utils.get_sim_time(units="ns")

            high_time = t_fall - t_rise
            period = t_next_rise - t_rise
            measured_duty = (high_time / period) * 100.0

            dut._log.info(f"Measured Duty Cycle: {measured_duty:.2f}%")
            assert abs(measured_duty - expected_pct) <= 1.0, (
                f"Duty cycle error: expected {expected_pct}%, got {measured_duty:.2f}%"
            )