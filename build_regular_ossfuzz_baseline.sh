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


# ── Function: Collect function coverage for harnessAgent-generated fuzzers ───
# Usage:
#   GEN_OUTPUT_DIR="outputs_cpp/gpt5-mini/evaluation_bloaty_full/" \
#   HAGENT_PROJECTS="bloaty" \
#   HAGENT_LOG_DIR="./logs/cov_hagent" \
#   bash build_regular_ossfuzz_baseline.sh   (with HAGENT_COV_ONLY=1 to only run this)
cov_func_collection_hagent() {
    local gen_output_dir="${GEN_OUTPUT_DIR:?GEN_OUTPUT_DIR must be set (e.g. outputs_cpp/gpt5-mini/evaluation_bloaty_full/)}"
    # Resolve relative paths against SCRIPT_DIR (cwd is OSS_FUZZ_DIR after cd)
    [[ "$gen_output_dir" != /* ]] && gen_output_dir="${SCRIPT_DIR}/${gen_output_dir}"
    local hagent_projects="${HAGENT_PROJECTS:-$PROJECTS}"
    local hagent_log_dir="${HAGENT_LOG_DIR:-${SCRIPT_DIR}/logs/cov_hagent}"

    echo "=== Collecting function coverage for harnessAgent-generated fuzzers ==="
    echo "GEN_OUTPUT_DIR=${gen_output_dir}"

    # Cache available docker images once
    local available_images
    available_images="$(docker images --format '{{.Repository}}')"

    for project in $hagent_projects; do
        local project_dir="${gen_output_dir%/}/${project}"
        if [[ ! -d "$project_dir" ]]; then
            echo "WARNING: project dir ${project_dir} does not exist, skipping ${project}."
            continue
        fi

        mkdir -p "${hagent_log_dir}/${project}"

        # Find all run directories (run1_*, run2_*, etc.)
        while IFS= read -r run_dir; do
            local build_id
            build_id="$(basename "$run_dir")"
            local func_name
            func_name="$(basename "$(dirname "$run_dir")")"

            # Check if docker image exists for this build_id
            if ! grep -qx "gcr.io/oss-fuzz/${build_id}" <<< "$available_images"; then
                echo "WARNING: no docker image gcr.io/oss-fuzz/${build_id}, skipping."
                continue
            fi

            local out_dir="${BUILD_OUT_DIR}/${build_id}"
            if [[ ! -d "$out_dir" ]]; then
                echo "WARNING: no build output dir ${out_dir}, skipping ${build_id}."
                continue
            fi

            # Discover valid fuzz targets in the build output
            local found_fuzzer=false
            for f in "$out_dir"/*; do
                [[ -f "$f" && -x "$f" ]] || continue
                local fname
                fname="$(basename "$f")"

                # Skip non-fuzzer artifacts
                case "$fname" in
                    afl-*|centipede|jazzer_*|llvm-symbolizer|*.py) continue ;;
                esac

                # Check that corpora directory exists (under GEN_OUTPUT_DIR)
                local corpus_dir="${run_dir}/corpora"
                if [[ ! -d "$corpus_dir" ]]; then
                    echo "WARNING: corpus dir ${corpus_dir} does not exist, skipping ${build_id}/${fname}."
                    continue
                fi

                local cov_log="${hagent_log_dir}/${project}/${func_name}_${build_id}_${ROUND_NUM}_coverage.log"
                echo "Collecting coverage: ${project}/${func_name}/${build_id} (target: ${fname}) → ${cov_log}"

                docker run --privileged --shm-size=2g --platform linux/amd64 --rm \
                    -e FUZZING_ENGINE=libfuzzer \
                    -e SANITIZER=address \
                    -e ARCHITECTURE=x86_64 \
                    -e HELPER=True \
                    -e PROJECT_NAME="$project" \
                    -e FUZZING_LANGUAGE=c++ \
                    -v "${out_dir}:/out" \
                    -v "${corpus_dir}:/corpus" \
                    -t "gcr.io/oss-fuzz/${build_id}" \
                    /bin/bash -c "/out/${fname} -print_coverage=1 -runs=0 /corpus/ 2>&1 | grep -w COVERED_FUNC" \
                    | tee -a "$cov_log"

                found_fuzzer=true
            done

            if [[ "$found_fuzzer" == false ]]; then
                echo "WARNING: no valid fuzz target found in ${out_dir}."
            fi
        done < <(find "$project_dir" -maxdepth 2 -name "run*_*" -type d)
    done

    echo "=== harnessAgent function coverage collection completed ==="
}


# ── Function: Rebuild all harnessAgent-generated targets ─────────────────────
# Usage:
#   GEN_OUTPUT_DIR="outputs_cpp/gpt5-mini/evaluation_bloaty_full/" \
#   HAGENT_PROJECTS="bloaty" \
#   HAGENT_REBUILD_ONLY=1 \
#   bash build_regular_ossfuzz_baseline.sh
rebuild_hagent_targets() {
    local gen_output_dir="${GEN_OUTPUT_DIR:?GEN_OUTPUT_DIR must be set (e.g. outputs_cpp/gpt5-mini/evaluation_bloaty_full/)}"
    [[ "$gen_output_dir" != /* ]] && gen_output_dir="${SCRIPT_DIR}/${gen_output_dir}"
    local hagent_projects="${HAGENT_PROJECTS:-$PROJECTS}"

    echo "=== Rebuilding harnessAgent-generated targets ==="
    echo "GEN_OUTPUT_DIR=${gen_output_dir}"

    local total=0 success=0 fail=0

    for project in $hagent_projects; do
        local project_dir="${gen_output_dir%/}/${project}"
        if [[ ! -d "$project_dir" ]]; then
            echo "WARNING: project dir ${project_dir} does not exist, skipping ${project}."
            continue
        fi

        while IFS= read -r run_dir; do
            local build_id
            build_id="$(basename "$run_dir")"
            local func_name
            func_name="$(basename "$(dirname "$run_dir")")"

            # Check that the project dir exists under oss-fuzz/projects/
            if [[ ! -d "${OSS_FUZZ_DIR}/projects/${build_id}" ]]; then
                echo "WARNING: no project dir projects/${build_id}, skipping."
                continue
            fi

            ((total++)) || true
            echo "Rebuilding: ${project}/${func_name}/${build_id}"
            python3 infra/helper.py build_image --no-pull "${build_id}"

            if python3 infra/helper.py build_fuzzers --clean "$build_id"; then
                echo "OK: ${build_id} rebuilt successfully."
                ((success++)) || true
            else
                echo "FAIL: ${build_id} build failed."
                ((fail++)) || true
            fi

            docker image prune -f
        done < <(find "$project_dir" -maxdepth 2 -name "run*_*" -type d)
    done

    echo "=== Rebuild completed: ${success}/${total} succeeded, ${fail} failed ==="
}

# ── HAGENT_REBUILD_ONLY mode: rebuild harnessAgent targets and exit ─────────
if [[ -n "${HAGENT_REBUILD_ONLY}" ]]; then
    echo "HAGENT_REBUILD_ONLY is set – rebuilding harnessAgent-generated targets only."
    rebuild_hagent_targets
    exit 0
fi 

# ── HAGENT_COV_ONLY mode: collect harnessAgent coverage and exit ────────────
if [[ -n "${HAGENT_COV_ONLY}" ]]; then
    echo "HAGENT_COV_ONLY is set – collecting harnessAgent-generated fuzzer coverage only."
    cov_func_collection_hagent
    exit 0
fi

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
