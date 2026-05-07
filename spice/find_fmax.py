#!/usr/bin/env python3
"""
Cycle-scaled SRAM fmax search for top.spi.

Usage:
  python3 find_fmax.py
  python3 find_fmax.py --min-period-ns 0.10 --max-period-ns 2.00 --tol-ns 0.005
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
from pathlib import Path


def build_control_block(period_ns: float) -> str:
    t_read0 = 2.5 * period_ns
    t_read1 = 3.5 * period_ns
    t_stop = 4.5 * period_ns
    t_step = max(period_ns / 200.0, 0.0005)  # ns, floor = 0.5ps
    td_meas = 2.0 * period_ns

    return f""".control
tran {t_step:.6f}n {t_stop:.6f}n
meas tran d0_r0 find v(Dout_0) at={t_read0:.6f}n
meas tran d1_r0 find v(Dout_1) at={t_read0:.6f}n
meas tran d2_r0 find v(Dout_2) at={t_read0:.6f}n
meas tran d3_r0 find v(Dout_3) at={t_read0:.6f}n
meas tran d0_r1 find v(Dout_0) at={t_read1:.6f}n
meas tran d1_r1 find v(Dout_1) at={t_read1:.6f}n
meas tran d2_r1 find v(Dout_2) at={t_read1:.6f}n
meas tran d3_r1 find v(Dout_3) at={t_read1:.6f}n
meas tran t_clk_to_dout trig v(CLK) val=0.5 rise=3 targ v(Dout_0) val=0.5 cross=1 td={td_meas:.6f}n
print d3_r0 d2_r0 d1_r0 d0_r0 d3_r1 d2_r1 d1_r1 d0_r1 t_clk_to_dout
.endc
.END
"""


def make_deck(template: str, period_ns: float) -> str:
    # Clock source scaled by period.
    t_start = max(0.02 * period_ns, 0.005)  # ns
    t_edge = max(0.01 * period_ns, 0.002)   # ns
    t_pw = max(0.48 * period_ns, 0.005)     # ns
    vclk = (
        f"Vclk CLK 0 PULSE(0 1.0 {t_start:.6f}n {t_edge:.6f}n {t_edge:.6f}n "
        f"{t_pw:.6f}n {period_ns:.6f}n)"
    )

    # Address/data/WE schedule in cycles:
    # C0 write addr0 data=0x5
    # C1 write addr1 data=0xA
    # C2 read addr0
    # C3 read addr1
    dt = max(0.01 * period_ns, 0.002)  # transition offset in ns
    p = period_ns

    va3 = "Va3 A_3 0 PWL(0 0  1000n 0)"
    va2 = "Va2 A_2 0 PWL(0 0  1000n 0)"
    va1 = "Va1 A_1 0 PWL(0 0  1000n 0)"
    va0 = (
        "Va0 A_0 0 PWL("
        f"0 0  {1*p:.6f}n 0  {(1*p+dt):.6f}n 1  "
        f"{2*p:.6f}n 1  {(2*p+dt):.6f}n 0  "
        f"{3*p:.6f}n 0  {(3*p+dt):.6f}n 1  "
        f"{4*p:.6f}n 1  {(4*p+dt):.6f}n 0  1000n 0)"
    )

    vd3 = (
        "Vd3 Din_3 0 PWL("
        f"0 0  {1*p:.6f}n 0  {(1*p+dt):.6f}n 1  "
        f"{4*p:.6f}n 1  {(4*p+dt):.6f}n 1  1000n 1)"
    )
    vd2 = (
        "Vd2 Din_2 0 PWL("
        f"0 1  {1*p:.6f}n 1  {(1*p+dt):.6f}n 0  "
        f"{4*p:.6f}n 0  {(4*p+dt):.6f}n 1  1000n 1)"
    )
    vd1 = (
        "Vd1 Din_1 0 PWL("
        f"0 0  {1*p:.6f}n 0  {(1*p+dt):.6f}n 1  "
        f"{4*p:.6f}n 1  {(4*p+dt):.6f}n 1  1000n 1)"
    )
    vd0 = (
        "Vd0 Din_0 0 PWL("
        f"0 1  {1*p:.6f}n 1  {(1*p+dt):.6f}n 0  "
        f"{4*p:.6f}n 0  {(4*p+dt):.6f}n 1  1000n 1)"
    )

    # Keep WE high through first two write cycles, low through two read cycles.
    we_fall = 1.8 * p
    vwe = (
        "Vwe WE 0 PWL("
        f"0 1  {we_fall:.6f}n 1  {(we_fall+dt):.6f}n 0  "
        f"{4*p:.6f}n 0  {(4*p+dt):.6f}n 1  1000n 1)"
    )

    out = template
    replacements = {
        r"^Vclk CLK 0 PULSE\([^\n]+\)$": vclk,
        r"^Va3 A_3 0 PWL\([^\n]+\)$": va3,
        r"^Va2 A_2 0 PWL\([^\n]+\)$": va2,
        r"^Va1 A_1 0 PWL\([^\n]+\)$": va1,
        r"^Va0 A_0 0 PWL\([^\n]+\)$": va0,
        r"^Vd3 Din_3 0 PWL\([^\n]+\)$": vd3,
        r"^Vd2 Din_2 0 PWL\([^\n]+\)$": vd2,
        r"^Vd1 Din_1 0 PWL\([^\n]+\)$": vd1,
        r"^Vd0 Din_0 0 PWL\([^\n]+\)$": vd0,
        r"^Vwe WE 0 PWL\([^\n]+\)$": vwe,
    }
    for pat, repl in replacements.items():
        out, n = re.subn(pat, repl, out, flags=re.M)
        if n != 1:
            raise RuntimeError(f"Expected one match for pattern: {pat}")

    if ".control" not in out:
        raise RuntimeError("Template missing .control block")
    prefix = out.split(".control", 1)[0]
    return prefix + build_control_block(period_ns)


def parse_measure(stdout: str, name: str, required: bool = True) -> float:
    m = re.search(rf"{re.escape(name)}\s*=\s*([+\-0-9.eE]+)", stdout)
    if not m:
        if required:
            raise RuntimeError(f"Missing measure: {name}")
        return float("nan")
    return float(m.group(1))


def run_case(base_text: str, base_dir: Path, period_ns: float) -> tuple[bool, dict]:
    deck = make_deck(base_text, period_ns)
    tmp = base_dir / "top_fmax_tmp.spi"
    tmp.write_text(deck)
    try:
        proc = subprocess.run(
            ["ngspice", "-b", tmp.name],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout + "\n" + proc.stderr
        vals = {}
        for key in ("d0_r0", "d1_r0", "d2_r0", "d3_r0", "d0_r1", "d1_r1", "d2_r1", "d3_r1"):
            vals[key] = parse_measure(out, key, required=True)
        vals["t_clk_to_dout"] = parse_measure(out, "t_clk_to_dout", required=False)

        # Threshold analog outputs at 0.5V.
        b = {k: int(v > 0.5) for k, v in vals.items() if k.startswith("d")}
        pass_r0 = (b["d3_r0"], b["d2_r0"], b["d1_r0"], b["d0_r0"]) == (0, 1, 0, 1)  # 0x5
        pass_r1 = (b["d3_r1"], b["d2_r1"], b["d1_r1"], b["d0_r1"]) == (1, 0, 1, 0)  # 0xA
        ok = (proc.returncode == 0) and pass_r0 and pass_r1
        vals["pass_r0"] = pass_r0
        vals["pass_r1"] = pass_r1
        vals["returncode"] = proc.returncode
        return ok, vals
    finally:
        if tmp.exists():
            tmp.unlink()


def find_fmax(base_text: str, base_dir: Path, min_period_ns: float, max_period_ns: float, tol_ns: float):
    # Ensure bracket: max_period should pass, min_period should fail (ideally).
    ok_hi, vals_hi = run_case(base_text, base_dir, max_period_ns)
    ok_lo, vals_lo = run_case(base_text, base_dir, min_period_ns)

    if not ok_hi:
        raise RuntimeError(
            f"Even max period {max_period_ns}ns fails (r0={vals_hi['pass_r0']} r1={vals_hi['pass_r1']})."
        )
    if ok_lo:
        return min_period_ns, vals_lo, vals_hi, []  # Already passes at fastest requested.

    history = []
    lo = min_period_ns  # fail
    hi = max_period_ns  # pass
    while (hi - lo) > tol_ns:
        mid = 0.5 * (hi + lo)
        ok, vals = run_case(base_text, base_dir, mid)
        history.append((mid, ok, vals))
        if ok:
            hi = mid
            vals_hi = vals
        else:
            lo = mid
            vals_lo = vals

    return hi, vals_hi, vals_lo, history


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default="top.spi", help="Base deck file (default: top.spi)")
    ap.add_argument("--min-period-ns", type=float, default=0.10, help="Fast bound (expected fail)")
    ap.add_argument("--max-period-ns", type=float, default=2.00, help="Slow bound (expected pass)")
    ap.add_argument("--tol-ns", type=float, default=0.005, help="Binary-search stop tolerance in ns")
    args = ap.parse_args()

    deck_path = Path(args.deck).resolve()
    if not deck_path.exists():
        raise SystemExit(f"Deck not found: {deck_path}")

    base_text = deck_path.read_text()
    best_period, best_vals, fail_vals, _history = find_fmax(
        base_text=base_text,
        base_dir=deck_path.parent,
        min_period_ns=args.min_period_ns,
        max_period_ns=args.max_period_ns,
        tol_ns=args.tol_ns,
    )

    freq_ghz = 1.0 / best_period
    print("=== FMAX RESULT (cycle-scaled bench) ===")
    print(f"best_pass_period_ns: {best_period:.6f}")
    print(f"best_pass_freq_GHz:  {freq_ghz:.3f}")
    print(f"read0_pass:          {best_vals['pass_r0']}")
    print(f"read1_pass:          {best_vals['pass_r1']}")
    print(f"t_clk_to_dout_s:     {best_vals['t_clk_to_dout']:.6e}")
    print("--- nearest failing corner info ---")
    print(f"fail_read0_pass:     {fail_vals['pass_r0']}")
    print(f"fail_read1_pass:     {fail_vals['pass_r1']}")


if __name__ == "__main__":
    main()

