#!/usr/bin/env python3
"""
Cycle-scaled SRAM f_max search for top.spi.

Binary-searches minimum CLK period where a repeating W/W/R/R macro pattern
passes functional readback. Optionally re-runs many macro cycles at T_min to
confirm steady-state operation (stronger "sustained f_max" claim).

Usage:
  python3 find_fmax.py              # one-line summary on stdout
  python3 find_fmax.py --json       # metrics only (JSON)
  python3 find_fmax.py --verify-macro-cycles 48 --json
  python3 find_fmax.py --min-period-ns 0.10 --max-period-ns 2.00 --tol-ns 0.002
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

SPEC_FMIN_GHZ = 0.5
VDD = 1.0
# Expected readback on Dout_0..3 (LSB..MSB wiring in meas order b*r*{0..3}).
READ0_NIBBLE = (1, 0, 1, 0)  # 0x5
READ1_NIBBLE = (0, 1, 0, 1)  # 0xA


def ngspice_version_line() -> str:
    try:
        proc = subprocess.run(
            ["ngspice", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        text = (proc.stdout + proc.stderr).strip().splitlines()
        return text[0] if text else "ngspice (version unknown)"
    except (OSError, subprocess.TimeoutExpired):
        return "ngspice (not found)"


def _format_pwl_line(prefix: str, points: list[tuple[float, float]], tail_t: float, tail_v: float) -> str:
    """Collapse duplicate timestamps (last wins), sort, append dc tail point."""
    by_t: dict[float, float] = {}
    for t, v in points:
        by_t[t] = v
    by_t[tail_t] = tail_v
    ordered = sorted(by_t.items())
    parts = [prefix + "("]
    for i, (t, v) in enumerate(ordered):
        sep = "" if i == 0 else " "
        parts.append(f"{sep}{t:.6f}n {v:.6f}")
    parts.append(")")
    return "".join(parts)


def build_pwl_sources(period_ns: float, macro_cycles: int) -> dict[str, str]:
    """PWL sources for K repeats of: write addr0 0x5, write addr1 0xA, read, read."""
    p = period_ns
    dt = max(0.01 * period_ns, 0.002)
    k = macro_cycles
    t_start = max(0.02 * period_ns, 0.005)
    t_edge = max(0.01 * period_ns, 0.002)
    t_pw = max(0.48 * period_ns, 0.005)
    we_fall = 1.8 * p

    vclk = (
        f"Vclk CLK 0 PULSE(0 {VDD:.1f} {t_start:.6f}n {t_edge:.6f}n {t_edge:.6f}n "
        f"{t_pw:.6f}n {period_ns:.6f}n)"
    )

    va3 = "Va3 A_3 0 PWL(0 0  1000n 0)"
    va2 = "Va2 A_2 0 PWL(0 0  1000n 0)"
    va1 = "Va1 A_1 0 PWL(0 0  1000n 0)"

    def rel_va0(chain_to_next: bool) -> list[tuple[float, float]]:
        pts = [
            (0.0, 0.0),
            (p, 0.0),
            (p + dt, 1.0),
            (2 * p, 1.0),
            (2 * p + dt, 0.0),
            (3 * p, 0.0),
            (3 * p + dt, 1.0),
        ]
        if chain_to_next:
            pts.append((4 * p, 0.0))
        else:
            pts.extend([(4 * p, 1.0), (4 * p + dt, 0.0)])
        return pts

    def rel_vd3(chain: bool) -> list[tuple[float, float]]:
        pts = [(0.0, 0.0), (p, 0.0), (p + dt, 1.0)]
        if chain:
            pts.append((4 * p, 0.0))
        else:
            pts.extend([(4 * p, 1.0), (4 * p + dt, 1.0)])
        return pts

    def rel_vd2(chain: bool) -> list[tuple[float, float]]:
        pts = [(0.0, 1.0), (p, 1.0), (p + dt, 0.0)]
        if chain:
            pts.append((4 * p, 1.0))
        else:
            pts.extend([(4 * p, 0.0), (4 * p + dt, 1.0)])
        return pts

    def rel_vd1(chain: bool) -> list[tuple[float, float]]:
        pts = [(0.0, 0.0), (p, 0.0), (p + dt, 1.0)]
        if chain:
            pts.append((4 * p, 0.0))
        else:
            pts.extend([(4 * p, 1.0), (4 * p + dt, 1.0)])
        return pts

    def rel_vd0(chain: bool) -> list[tuple[float, float]]:
        pts = [(0.0, 1.0), (p, 1.0), (p + dt, 0.0)]
        if chain:
            pts.append((4 * p, 1.0))
        else:
            pts.extend([(4 * p, 0.0), (4 * p + dt, 1.0)])
        return pts

    abs_a0: list[tuple[float, float]] = []
    abs_d3: list[tuple[float, float]] = []
    abs_d2: list[tuple[float, float]] = []
    abs_d1: list[tuple[float, float]] = []
    abs_d0: list[tuple[float, float]] = []
    for r in range(k):
        t_off = r * 4 * p
        chain = r < k - 1
        abs_a0.extend((t_off + tr, vr) for tr, vr in rel_va0(chain))
        abs_d3.extend((t_off + tr, vr) for tr, vr in rel_vd3(chain))
        abs_d2.extend((t_off + tr, vr) for tr, vr in rel_vd2(chain))
        abs_d1.extend((t_off + tr, vr) for tr, vr in rel_vd1(chain))
        abs_d0.extend((t_off + tr, vr) for tr, vr in rel_vd0(chain))

    va0 = _format_pwl_line("Va0 A_0 0 PWL", abs_a0, 1000.0, 0.0)
    vd3 = _format_pwl_line("Vd3 Din_3 0 PWL", abs_d3, 1000.0, 1.0)
    vd2 = _format_pwl_line("Vd2 Din_2 0 PWL", abs_d2, 1000.0, 1.0)
    vd1 = _format_pwl_line("Vd1 Din_1 0 PWL", abs_d1, 1000.0, 1.0)
    vd0 = _format_pwl_line("Vd0 Din_0 0 PWL", abs_d0, 1000.0, 1.0)

    parts_we = ["Vwe WE 0 PWL("]
    for r in range(k):
        t0 = r * 4 * p
        if r == 0:
            parts_we.append("0 1")
        parts_we.append(
            f" {t0 + we_fall:.6f}n 1  {(t0 + we_fall + dt):.6f}n 0"
        )
        if r < k - 1:
            parts_we.append(f"  {(t0 + 4 * p):.6f}n 1")
        else:
            parts_we.append(
                f" {t0 + 4 * p:.6f}n 0  {(t0 + 4 * p + dt):.6f}n 1  1000n 1"
            )
    parts_we.append(")")
    vwe = "".join(parts_we)

    return {
        "vclk": vclk,
        "va3": va3,
        "va2": va2,
        "va1": va1,
        "va0": va0,
        "vd3": vd3,
        "vd2": vd2,
        "vd1": vd1,
        "vd0": vd0,
        "vwe": vwe,
    }


def build_control_block(
    period_ns: float,
    macro_cycles: int,
    *,
    include_clk_to_dout: bool,
) -> str:
    p = period_ns
    k = macro_cycles
    t_stop = k * 4 * p + 0.5 * p
    t_step = max(period_ns / 200.0, 0.0005)
    lines = [
        ".control",
        f"tran {t_step:.6f}n {t_stop:.6f}n",
    ]

    print_names: list[str] = []
    for r in range(k):
        t_read0 = r * 4 * p + 2.5 * p
        t_read1 = r * 4 * p + 3.5 * p
        for j in range(4):
            name = f"b{r}a{j}"
            lines.append(f"meas tran {name} find v(Dout_{j}) at={t_read0:.6f}n")
            print_names.append(name)
        for j in range(4):
            name = f"b{r}b{j}"
            lines.append(f"meas tran {name} find v(Dout_{j}) at={t_read1:.6f}n")
            print_names.append(name)

    td_meas = 2.0 * period_ns
    if include_clk_to_dout:
        lines.append(
            "meas tran t_clk_to_dout trig v(CLK) val=0.5 rise=3 "
            f"targ v(Dout_0) val=0.5 cross=1 td={td_meas:.6f}n"
        )
        print_names.append("t_clk_to_dout")

    lines.append("print " + " ".join(print_names))
    lines.extend([".endc", ".END", ""])
    return "\n".join(lines)


def make_deck(template: str, period_ns: float, macro_cycles: int = 1) -> str:
    src = build_pwl_sources(period_ns, macro_cycles)
    out = template
    replacements = {
        r"^Vclk CLK 0 PULSE\([^\n]+\)$": src["vclk"],
        r"^Va3 A_3 0 PWL\([^\n]+\)$": src["va3"],
        r"^Va2 A_2 0 PWL\([^\n]+\)$": src["va2"],
        r"^Va1 A_1 0 PWL\([^\n]+\)$": src["va1"],
        r"^Va0 A_0 0 PWL\([^\n]+\)$": src["va0"],
        r"^Vd3 Din_3 0 PWL\([^\n]+\)$": src["vd3"],
        r"^Vd2 Din_2 0 PWL\([^\n]+\)$": src["vd2"],
        r"^Vd1 Din_1 0 PWL\([^\n]+\)$": src["vd1"],
        r"^Vd0 Din_0 0 PWL\([^\n]+\)$": src["vd0"],
        r"^Vwe WE 0 PWL\([^\n]+\)$": src["vwe"],
    }
    for pat, repl in replacements.items():
        n = 0
        out, n = re.subn(pat, repl, out, flags=re.M)
        if n != 1:
            raise RuntimeError(f"Expected one match for pattern: {pat}")

    if ".control" not in out:
        raise RuntimeError("Template missing .control block")
    prefix = out.split(".control", 1)[0]
    ctrl = build_control_block(
        period_ns,
        macro_cycles,
        include_clk_to_dout=(macro_cycles == 1),
    )
    return prefix + ctrl


def parse_measure(stdout: str, name: str, required: bool = True) -> float:
    m = re.search(rf"{re.escape(name)}\s*=\s*([+\-0-9.eE]+)", stdout)
    if not m:
        if required:
            raise RuntimeError(f"Missing measure: {name}")
        return float("nan")
    return float(m.group(1))


def _bits_from_block(out: str, r: int, phase: str) -> tuple[int, int, int, int]:
    suffix = "a" if phase == "a" else "b"
    return tuple(
        int(parse_measure(out, f"b{r}{suffix}{j}") > 0.5) for j in range(4)
    )


def evaluate_macro_pattern(out: str, macro_cycles: int, proc_rc: int) -> tuple[bool, dict]:
    failed_blocks: list[int] = []
    for r in range(macro_cycles):
        ba = _bits_from_block(out, r, "a")
        bb = _bits_from_block(out, r, "b")
        ok_a = ba == READ0_NIBBLE
        ok_b = bb == READ1_NIBBLE
        if not (ok_a and ok_b):
            failed_blocks.append(r)

    meta = {
        "failed_block_indices": failed_blocks,
        "pass_all": len(failed_blocks) == 0 and proc_rc == 0,
    }
    return meta["pass_all"], meta


def run_case(
    base_text: str,
    base_dir: Path,
    period_ns: float,
    macro_cycles: int = 1,
) -> tuple[bool, dict]:
    deck = make_deck(base_text, period_ns, macro_cycles)
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
        vals: dict = {"returncode": proc.returncode}

        for r in range(macro_cycles):
            for j in range(4):
                vals[f"b{r}a{j}"] = parse_measure(out, f"b{r}a{j}", required=True)
                vals[f"b{r}b{j}"] = parse_measure(out, f"b{r}b{j}", required=True)

        if macro_cycles == 1:
            vals["t_clk_to_dout"] = parse_measure(out, "t_clk_to_dout", required=False)
            # Legacy keys for callers / logs
            vals["d0_r0"] = vals["b0a0"]
            vals["d1_r0"] = vals["b0a1"]
            vals["d2_r0"] = vals["b0a2"]
            vals["d3_r0"] = vals["b0a3"]
            vals["d0_r1"] = vals["b0b0"]
            vals["d1_r1"] = vals["b0b1"]
            vals["d2_r1"] = vals["b0b2"]
            vals["d3_r1"] = vals["b0b3"]

        ok_pattern, meta = evaluate_macro_pattern(out, macro_cycles, proc.returncode)
        vals.update(meta)
        if macro_cycles >= 1:
            ba = _bits_from_block(out, 0, "a")
            bb = _bits_from_block(out, 0, "b")
            vals["pass_r0"] = ba == READ0_NIBBLE
            vals["pass_r1"] = bb == READ1_NIBBLE
        else:
            vals["pass_r0"] = False
            vals["pass_r1"] = False
        ok = ok_pattern
        return ok, vals
    finally:
        if tmp.exists():
            tmp.unlink()


def find_fmax(
    base_text: str,
    base_dir: Path,
    min_period_ns: float,
    max_period_ns: float,
    tol_ns: float,
) -> tuple[float, dict, dict, list]:
    ok_hi, vals_hi = run_case(base_text, base_dir, max_period_ns, macro_cycles=1)
    ok_lo, vals_lo = run_case(base_text, base_dir, min_period_ns, macro_cycles=1)

    if not ok_hi:
        raise RuntimeError(
            f"Even max period {max_period_ns}ns fails "
            f"(failed_blocks={vals_hi.get('failed_block_indices')})."
        )
    if ok_lo:
        return min_period_ns, vals_lo, vals_hi, []

    history = []
    lo = min_period_ns
    hi = max_period_ns
    while (hi - lo) > tol_ns:
        mid = 0.5 * (hi + lo)
        ok, vals = run_case(base_text, base_dir, mid, macro_cycles=1)
        history.append((mid, ok, vals))
        if ok:
            hi = mid
            vals_hi = vals
        else:
            lo = mid
            vals_lo = vals

    return hi, vals_hi, vals_lo, history


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Binary-search min CLK period for repeating W/W/R/R SRAM test."
    )
    ap.add_argument("--deck", default="top.spi", help="Base deck (default: top.spi)")
    ap.add_argument("--min-period-ns", type=float, default=0.10, help="Fast bound (fail)")
    ap.add_argument("--max-period-ns", type=float, default=2.00, help="Slow bound (pass)")
    ap.add_argument(
        "--tol-ns",
        type=float,
        default=0.005,
        help="Bisection stop: period bracket width (ns)",
    )
    ap.add_argument(
        "--verify-macro-cycles",
        type=int,
        default=32,
        help=(
            "After search, re-run this many W/W/R/R macros at T_min (0=skip). "
            "Default 32 ≈ 128 CLK cycles."
        ),
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Print only a JSON object with key metrics (no other stdout).",
    )
    args = ap.parse_args()

    deck_path = Path(args.deck).resolve()
    if not deck_path.exists():
        raise SystemExit(f"Deck not found: {deck_path}")

    ngspice_v = ngspice_version_line()
    base_text = deck_path.read_text()
    best_period, best_vals, _fail_vals, _history = find_fmax(
        base_text=base_text,
        base_dir=deck_path.parent,
        min_period_ns=args.min_period_ns,
        max_period_ns=args.max_period_ns,
        tol_ns=args.tol_ns,
    )

    freq_ghz = 1.0 / best_period
    tmin_ps = best_period * 1e3
    margin = freq_ghz / SPEC_FMIN_GHZ
    tol_ps = args.tol_ns * 1e3

    steady_ok: bool | None = None
    steady_vals: dict = {}
    if args.verify_macro_cycles > 1:
        steady_ok, steady_vals = run_case(
            base_text,
            deck_path.parent,
            best_period,
            macro_cycles=args.verify_macro_cycles,
        )
    elif args.verify_macro_cycles == 1:
        steady_ok = bool(best_vals.get("pass_all", True))
    else:
        steady_ok = None

    n_clk_steady = (
        args.verify_macro_cycles * 4 if args.verify_macro_cycles > 1 else 0
    )
    n_read_checks = (
        2 * args.verify_macro_cycles if args.verify_macro_cycles > 1 else 2
    )
    result_obj = {
        "sustained_fmax_ghz": round(freq_ghz, 6),
        "t_min_clk_ps": round(tmin_ps, 4),
        "t_min_clk_ns": round(best_period, 6),
        "spec_fmin_ghz": SPEC_FMIN_GHZ,
        "margin_vs_spec_x": round(margin, 4),
        "vdd_v": VDD,
        "search_tol_ps": round(tol_ps, 4),
        "verify_macro_cycles": args.verify_macro_cycles,
        "steady_state_clk_cycles": n_clk_steady,
        "steady_state_readback_checks": n_read_checks,
        "steady_state_verify_pass": steady_ok,
        "ngspice": ngspice_v,
        "pattern": "W/W/R/R addr0=0x5 addr1=0xA functional readback @0.5V",
    }

    tcd = best_vals.get("t_clk_to_dout")
    if tcd is not None and tcd == tcd:
        result_obj["t_clk_to_dout_s"] = float(tcd)
        result_obj["t_clk_to_dout_ps"] = round(float(tcd) * 1e12, 4)

    if args.json:
        print(json.dumps(result_obj, indent=2))
        return

    parts = [
        f"T_min_ns={best_period:.6f}",
        f"f_max_GHz={freq_ghz:.6f}",
        f"vs_{SPEC_FMIN_GHZ}GHz={margin:.3f}x",
    ]
    if args.verify_macro_cycles > 1:
        parts.append(f"steady_pass={steady_ok}")
    print(" ".join(parts))


if __name__ == "__main__":
    main()
