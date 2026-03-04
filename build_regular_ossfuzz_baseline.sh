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
CORPUS_BASE_DIR="${OSS_FUZZ_DIR}/build/corpus"

mkdir -p "$LOG_DIR"
cd "$OSS_FUZZ_DIR"

# ── Helper: Discover valid fuzz targets per project ─────────────────────────
discover_fuzzers() {
    declare -gA PROJECT_FUZZERS

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
}

# ── Function: Collect function coverage for all valid fuzzers ───────────────
cov_func_collection() {
    echo "=== Collecting function coverage ==="

    for project in $PROJECTS; do
        [[ -n "${PROJECT_FUZZERS[$project]}" ]] || continue

        mkdir -p "$LOG_DIR/$project"

        for fuzzer in ${PROJECT_FUZZERS[$project]}; do
            corpus_dir="${CORPUS_BASE_DIR}/${project}_${fuzzer}"

            if [[ ! -d "$corpus_dir" ]]; then
                echo "WARNING: corpus dir ${corpus_dir} does not exist, skipping ${project}/${fuzzer}."
                continue
            fi

            cov_log="${LOG_DIR}/${project}/${fuzzer}_${ROUND_NUM}_coverage.log"
            echo "Collecting coverage: ${project}/${fuzzer} → ${cov_log}"

            docker run --privileged --shm-size=2g --platform linux/amd64 --rm \
                -e FUZZING_ENGINE=libfuzzer \
                -e SANITIZER=address \
                -e ARCHITECTURE=x86_64 \
                -e HELPER=True \
                -e PROJECT_NAME="$project" \
                -e FUZZING_LANGUAGE=c++ \
                -v "$PWD/build/out/$project:/out" \
                -v "$PWD/build/work/$project:/work" \
                -v "${corpus_dir}:/corpus" \
                -t "gcr.io/oss-fuzz/$project" \
                /bin/bash -c "/out/$fuzzer -print_coverage=1 -runs=0 /corpus/ 2>&1 | grep -w COVERED_FUNC" \
                | tee "$cov_log"
        done
    done

    echo "=== Function coverage collection completed ==="
}

# ── FUNC_COV_ONLY mode: collect coverage and exit ───────────────────────────
if [[ -n "${FUNC_COV_ONLY}" ]]; then
    echo "FUNC_COV_ONLY is set – skipping build & fuzz, collecting coverage only."
    discover_fuzzers
    cov_func_collection
    exit 0
fi

# ── Step 1: Build all projects ──────────────────────────────────────────────
for project in $PROJECTS; do
    python3 infra/helper.py build_image --no-pull "$project"
    python3 infra/helper.py build_fuzzers --sanitizer=address "$project"
done

# ── Step 2: Discover valid fuzz targets per project ─────────────────────────
discover_fuzzers

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

# ── Step 4: Collect function coverage ───────────────────────────────────────
cov_func_collection
