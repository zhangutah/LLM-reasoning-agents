#!/bin/bash -e

PROJECTS="bloaty cppcheck hunspell libraw qpdf"
# boringssl
FUZZ_TIME="${FUZZ_TIME:-3600}"
ROUND_NUM="${ROUND_NUM:-1}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"

if [[ -f "infra/helper.py" ]]; then
    OSS_FUZZ_DIR="$(pwd)"
else
    OSS_FUZZ_DIR="$(cd "$(dirname "$0")/oss-fuzz" && pwd)"
fi
BUILD_OUT_DIR="${OSS_FUZZ_DIR}/build/out"

mkdir -p "$LOG_DIR"
cd "$OSS_FUZZ_DIR"

# ── Step 1: Build all projects ──────────────────────────────────────────────
for project in $PROJECTS; do
    python3 infra/helper.py build_image --no-pull "$project"
    python3 infra/helper.py build_fuzzers --sanitizer=address "$project"
done

# ── Step 2: Discover valid fuzz targets per project ─────────────────────────
declare -A PROJECT_FUZZERS

for project in $PROJECTS; do
    out_dir="${BUILD_OUT_DIR}/${project}"
    if [[ ! -d "$out_dir" ]]; then
        echo "WARNING: no build output for ${project}, skipping."
        continue
    fi

    fuzzers=()
    for f in "$out_dir"/*; do
        [[ -f "$f" && -x "$f" ]] || continue
        name="$(basename "$f")"

        # Skip non-fuzzer artifacts (same filters as OSS-Fuzz helper.py)
        case "$name" in
            afl-*|centipede|jazzer_*|llvm-symbolizer) continue ;;
        esac

        # Validate with check_build
        if python3 infra/helper.py check_build "$project" "$name"; then
            fuzzers+=("$name")
        else
            echo "WARNING: ${project}/${name} failed check_build, skipping."
        fi
    done

    if [[ ${#fuzzers[@]} -eq 0 ]]; then
        echo "WARNING: no valid fuzz targets for ${project}."
    else
        PROJECT_FUZZERS[$project]="${fuzzers[*]}"
        echo "INFO: ${project} fuzzers: ${fuzzers[*]}"
    fi
done

# ── Step 3: Run all valid fuzzers in parallel ───────────────────────────────
pids=()
for project in $PROJECTS; do
    for fuzzer in ${PROJECT_FUZZERS[$project]}; do
        log_file="${LOG_DIR}/${project}_${fuzzer}_${ROUND_NUM}.log"
        echo "Launching: ${project}/${fuzzer} for ${FUZZ_TIME}s → ${log_file}"
        echo "saving corpus to ./build/corpus/${project}_${fuzzer}/"
        mkdir -p "./build/corpus/${project}_${fuzzer}/"
        python3 infra/helper.py run_fuzzer --corpus-dir "./build/corpus/${project}_${fuzzer}" "$project" "$fuzzer" -- \
            -max_total_time="$FUZZ_TIME" &> "$log_file" &
        pids+=($!)
    done
done

echo "Waiting for ${#pids[@]} fuzzer(s) to finish …"
wait
echo "All fuzzing runs completed."


# coverage collection

# General format inside the container
# /out/<fuzzer_name> -print_coverage=1 -runs=0 /path/to/corpus/ 2>&1 | grep COVERED_FUNC

# Example for bloaty, looking at a specific source file
# /out/fuzz_target -print_coverage=1 -runs=0 /out/corpus/ 2>&1 | grep bloaty.cc | grep -w COVERED_FUNC
