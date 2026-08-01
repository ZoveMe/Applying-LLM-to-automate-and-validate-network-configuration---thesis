#!/usr/bin/env bash
#
# Reproducible live Week 7 multi-fault experiment.
#
# Normal first run:
#   bash scripts/run_topology_l_live.sh --setup
#
# Later runs with the already-pinned environment:
#   bash scripts/run_topology_l_live.sh
#
set -Eeuo pipefail
IFS=$'\n\t'

SETUP_ENVIRONMENT=0

usage() {
    cat <<'EOF'
Usage: bash scripts/run_topology_l_live.sh [OPTIONS]

Deploy the isolated Topology L lab, run the guarded three-fault Ansible
experiment, independently verify its evidence, and destroy the lab.

Options:
  --setup      Create/update the isolated .venv-ansible environment and
               install the exactly pinned Ansible collection before the run.
  -h, --help   Show this help without changing the system.

The experiment is interactive. Review the twelve checksum-bound actions and
type APPROVE_TOPOLOGY_L_REPAIR only if they match the intended repair.
EOF
}

while (($#)); do
    case "$1" in
        --setup)
            SETUP_ENVIRONMENT=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TOPOLOGY_FILE="benchmarks/topology-l/topology-l.clab.yml"
VENV_DIR="${TOPOLOGY_L_VENV:-.venv-ansible}"
COLLECTIONS_DIR="${TOPOLOGY_L_COLLECTIONS_PATH:-$VENV_DIR/collections}"
EVIDENCE_ROOT="docs/evidence/week7-ansible-multifault-runs"
DERIVED_ROOT="docs/derived/week7-ansible-multifault"

case "$VENV_DIR" in
    /*) ;;
    *) VENV_DIR="$REPO_ROOT/$VENV_DIR" ;;
esac
case "$COLLECTIONS_DIR" in
    /*) ;;
    *) COLLECTIONS_DIR="$REPO_ROOT/$COLLECTIONS_DIR" ;;
esac

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 ||
        die "required command not found: $1"
}

assert_clean_worktree() {
    local state
    state="$(git status --porcelain=v1 --untracked-files=all)"
    if [[ -n "$state" ]]; then
        printf '%s\n' "$state" >&2
        die "worktree must be clean before the live experiment"
    fi
}

require_command git
require_command python3
require_command docker
require_command containerlab
require_command sudo

ACTUAL_ROOT_RAW="$(git rev-parse --show-toplevel 2>/dev/null)" ||
    die "the script is not inside a Git repository"
ACTUAL_ROOT="$(cd -- "$ACTUAL_ROOT_RAW" 2>/dev/null && pwd -P)" ||
    die "cannot resolve the Git repository root: $ACTUAL_ROOT_RAW"
[[ "$ACTUAL_ROOT" == "$REPO_ROOT" ]] ||
    die "script root and Git repository root differ"
assert_clean_worktree

if ((SETUP_ENVIRONMENT == 1)); then
    printf '\n=== PREPARE PINNED ANSIBLE CONTROLLER ===\n'
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        python3 -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
    python -m pip install --upgrade pip
    python -m pip install -r requirements-ansible.txt
    export ANSIBLE_COLLECTIONS_PATH="$COLLECTIONS_DIR"
    ansible-galaxy collection install \
        -r ansible/requirements.yml \
        --force \
        -p "$COLLECTIONS_DIR"
else
    [[ -x "$VENV_DIR/bin/python" ]] ||
        die "missing $VENV_DIR; rerun with --setup"
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
    export ANSIBLE_COLLECTIONS_PATH="$COLLECTIONS_DIR"
fi

require_command ansible
require_command ansible-playbook
require_command ansible-galaxy

printf '\n=== LIVE PREFLIGHT ===\n'
docker info >/dev/null 2>&1 ||
    die "Docker daemon is unavailable to the current WSL user"
containerlab version
ansible --version
ansible-galaxy collection list community.docker
assert_clean_worktree

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD)"
RUN_DIR="$EVIDENCE_ROOT/$RUN_ID"
JSON_OUT="$DERIVED_ROOT/${RUN_ID}-verification.json"
MARKDOWN_OUT="$DERIVED_ROOT/${RUN_ID}-verification-mk.md"

[[ ! -e "$RUN_DIR" ]] || die "evidence directory already exists: $RUN_DIR"
[[ ! -e "$JSON_OUT" ]] || die "derived output already exists: $JSON_OUT"
[[ ! -e "$MARKDOWN_OUT" ]] ||
    die "derived output already exists: $MARKDOWN_OUT"

CLEANUP_REQUIRED=0
cleanup() {
    local status=$?
    trap - EXIT
    if ((CLEANUP_REQUIRED == 1)); then
        printf '\n=== DESTROY ISOLATED TOPOLOGY L LAB ===\n'
        if ! sudo containerlab destroy -t "$TOPOLOGY_FILE" --cleanup; then
            printf 'ERROR: Containerlab cleanup failed; inspect the lab manually.\n' >&2
            if ((status == 0)); then
                status=1
            fi
        fi
    fi
    exit "$status"
}
trap cleanup EXIT

printf '\n=== DEPLOY ISOLATED TOPOLOGY L LAB ===\n'
sudo -v
CLEANUP_REQUIRED=1
sudo containerlab deploy -t "$TOPOLOGY_FILE"

# Containerlab writes a generated clab-* directory. It must remain ignored so
# the evidence can be bound to the clean source commit after deployment.
assert_clean_worktree

printf '\n=== RUN GUARDED THREE-FAULT EXPERIMENT ===\n'
python3 -m experiments.topology_l_ansible_demo --output-dir "$RUN_DIR"

printf '\n=== INDEPENDENTLY VERIFY RAW EVIDENCE ===\n'
python3 -m experiments.verify_topology_l_ansible_evidence "$RUN_DIR" \
    --json-out "$JSON_OUT" \
    --markdown-out "$MARKDOWN_OUT"

printf '\nLIVE WEEK 7 EXPERIMENT: VERIFIED\n'
printf 'Raw evidence: %s\n' "$RUN_DIR"
printf 'Verification JSON: %s\n' "$JSON_OUT"
printf 'Macedonian summary: %s\n' "$MARKDOWN_OUT"
printf '\nGenerated evidence is intentionally uncommitted for manual review.\n'
git status --short
