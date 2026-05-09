#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <regex>
#include <sstream>
#include <string>
#include <vector>

struct EvalResult {
    double scale = 1.0;
    bool ok = false;
    double fmax_ghz = 0.0;
    double tmin_ns = 0.0;
    bool steady = false;
    std::string raw_json;
    std::string deck_path;
};

static std::string shell_escape(const std::string& s) {
    std::string out = "'";
    for (char c : s) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

static bool read_file(const std::string& path, std::string& out) {
    std::ifstream ifs(path);
    if (!ifs) return false;
    std::ostringstream ss;
    ss << ifs.rdbuf();
    out = ss.str();
    return true;
}

static bool write_file(const std::string& path, const std::string& text) {
    std::ofstream ofs(path);
    if (!ofs) return false;
    ofs << text;
    return static_cast<bool>(ofs);
}

static std::string scale_l1_widths(const std::string& spice, double scale, double min_w = 1.0) {
    static const std::regex pat(R"(L=1 W=([0-9]+(?:\.[0-9]+)?))");
    std::string out;
    out.reserve(spice.size() + 256);

    std::sregex_iterator it(spice.begin(), spice.end(), pat);
    std::sregex_iterator end;
    std::size_t last = 0;
    for (; it != end; ++it) {
        auto m = *it;
        out.append(spice, last, static_cast<std::size_t>(m.position()) - last);
        double w = std::stod(m[1].str());
        double nw = std::max(min_w, w * scale);
        nw = std::round(nw);
        std::ostringstream rep;
        rep << "L=1 W=" << std::fixed << std::setprecision(3) << nw;
        out += rep.str();
        last = static_cast<std::size_t>(m.position() + m.length());
    }
    out.append(spice, last, std::string::npos);
    return out;
}

static std::string run_cmd_capture(const std::string& cmd, int& rc) {
    std::array<char, 4096> buf{};
    std::string out;
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) {
        rc = -1;
        return out;
    }
    while (fgets(buf.data(), static_cast<int>(buf.size()), pipe)) {
        out += buf.data();
    }
    int status = pclose(pipe);
    if (status == -1) rc = -1;
    else rc = WEXITSTATUS(status);
    return out;
}

static bool extract_double(const std::string& s, const std::string& key, double& out) {
    std::regex pat("\"" + key + R"("\s*:\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?))");
    std::smatch m;
    if (!std::regex_search(s, m, pat)) return false;
    out = std::stod(m[1].str());
    return true;
}

static bool extract_bool(const std::string& s, const std::string& key, bool& out) {
    std::regex pat("\"" + key + R"("\s*:\s*(true|false))");
    std::smatch m;
    if (!std::regex_search(s, m, pat)) return false;
    out = (m[1].str() == "true");
    return true;
}

static EvalResult eval_scale(
    const std::string& base_spice,
    double scale,
    int verify_macro_cycles,
    double tol_ns,
    const std::string& work_dir,
    int idx
) {
    EvalResult r;
    r.scale = scale;
    std::ostringstream deck_name;
    deck_name << work_dir << "/spice/top_opt_tmp_" << idx << ".spi";
    r.deck_path = deck_name.str();

    std::string deck = scale_l1_widths(base_spice, scale);
    if (!write_file(r.deck_path, deck)) return r;

    std::ostringstream cmd;
    cmd << "python3 " << shell_escape(work_dir + "/spice/find_fmax.py")
        << " --deck " << shell_escape(r.deck_path)
        << " --verify-macro-cycles " << verify_macro_cycles
        << " --tol-ns " << std::fixed << std::setprecision(4) << tol_ns
        << " --json";

    int rc = 1;
    std::string out = run_cmd_capture(cmd.str(), rc);
    r.raw_json = out;
    if (rc != 0) return r;

    double fmax = 0.0, tmin = 0.0;
    bool steady = false;
    bool ok1 = extract_double(out, "sustained_fmax_ghz", fmax);
    bool ok2 = extract_double(out, "t_min_clk_ns", tmin);
    bool ok3 = extract_bool(out, "steady_state_verify_pass", steady);
    if (!(ok1 && ok2 && ok3)) return r;

    r.fmax_ghz = fmax;
    r.tmin_ns = tmin;
    r.steady = steady;
    r.ok = true;
    return r;
}

int main(int argc, char** argv) {
    std::string work_dir = ".";
    if (argc > 1) work_dir = argv[1];

    const std::string top_path = work_dir + "/spice/top.spi";
    std::string base_spice;
    if (!read_file(top_path, base_spice)) {
        std::cerr << "Failed to read " << top_path << "\n";
        return 1;
    }

    std::vector<double> scales = {
        1.00, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60,
        0.58, 0.56, 0.54, 0.52, 0.50, 0.48, 0.46, 0.44, 0.42, 0.40
    };

    std::vector<EvalResult> scout;
    std::cout << "Top-down scout (verify=2, tol=0.02ns)\n" << std::flush;
    for (std::size_t i = 0; i < scales.size(); ++i) {
        std::cout << "  [" << (i + 1) << "/" << scales.size() << "] scale="
                  << std::fixed << std::setprecision(2) << scales[i] << " running...\n"
                  << std::flush;
        EvalResult r = eval_scale(base_spice, scales[i], 2, 0.02, work_dir, static_cast<int>(i));
        scout.push_back(r);
        if (r.ok) {
            std::cout << "  scale=" << std::fixed << std::setprecision(2) << r.scale
                      << " fmax=" << std::setprecision(6) << r.fmax_ghz
                      << "GHz tmin=" << r.tmin_ns
                      << "ns steady=" << (r.steady ? "true" : "false") << "\n"
                      << std::flush;
        } else {
            std::cout << "  scale=" << std::fixed << std::setprecision(2) << r.scale
                      << " failed\n"
                      << std::flush;
        }
    }

    std::vector<EvalResult> valid;
    for (const auto& r : scout) if (r.ok && r.steady) valid.push_back(r);
    if (valid.empty()) {
        std::cerr << "No valid candidates found in scout.\n";
        return 2;
    }

    std::sort(valid.begin(), valid.end(), [](const EvalResult& a, const EvalResult& b) {
        return a.fmax_ghz > b.fmax_ghz;
    });

    const int finalists = std::min(4, static_cast<int>(valid.size()));
    std::vector<EvalResult> final_results;
    std::cout << "\nFinal verification (verify=32, tol=0.005ns)\n" << std::flush;
    for (int i = 0; i < finalists; ++i) {
        std::cout << "  final [" << (i + 1) << "/" << finalists << "] scale="
                  << std::fixed << std::setprecision(2) << valid[i].scale << " running...\n"
                  << std::flush;
        EvalResult r = eval_scale(base_spice, valid[i].scale, 32, 0.005, work_dir, 100 + i);
        final_results.push_back(r);
        if (r.ok) {
            std::cout << "  scale=" << std::fixed << std::setprecision(2) << r.scale
                      << " fmax=" << std::setprecision(6) << r.fmax_ghz
                      << "GHz tmin=" << r.tmin_ns
                      << "ns steady=" << (r.steady ? "true" : "false") << "\n"
                      << std::flush;
        } else {
            std::cout << "  scale=" << std::fixed << std::setprecision(2) << r.scale
                      << " failed\n"
                      << std::flush;
        }
    }

    std::vector<EvalResult> verified;
    for (const auto& r : final_results) if (r.ok && r.steady) verified.push_back(r);
    if (verified.empty()) {
        std::cerr << "No finalists passed strong verification.\n";
        return 3;
    }
    std::sort(verified.begin(), verified.end(), [](const EvalResult& a, const EvalResult& b) {
        return a.fmax_ghz > b.fmax_ghz;
    });
    const EvalResult& best = verified.front();

    std::string best_deck = work_dir + "/spice/top_opt_best.spi";
    write_file(best_deck, scale_l1_widths(base_spice, best.scale));

    std::ofstream csv(work_dir + "/spice/opt_fmax_results.csv");
    csv << "phase,scale,fmax_ghz,tmin_ns,steady,ok\n";
    for (const auto& r : scout) {
        csv << "scout," << r.scale << "," << r.fmax_ghz << "," << r.tmin_ns << ","
            << (r.steady ? "true" : "false") << "," << (r.ok ? "true" : "false") << "\n";
    }
    for (const auto& r : final_results) {
        csv << "final," << r.scale << "," << r.fmax_ghz << "," << r.tmin_ns << ","
            << (r.steady ? "true" : "false") << "," << (r.ok ? "true" : "false") << "\n";
    }

    std::cout << "\nBEST_VERIFIED scale=" << std::fixed << std::setprecision(3) << best.scale
              << " fmax=" << std::setprecision(6) << best.fmax_ghz
              << "GHz tmin=" << best.tmin_ns << "ns\n";
    std::cout << "Wrote: spice/top_opt_best.spi and spice/opt_fmax_results.csv\n";
    return 0;
}
