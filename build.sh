#!/usr/bin/env bash
# BSD Zero Clause License
# Copyright (c) 2026 Wouter Snels
set -euo pipefail

# Always operate from the repo root, regardless of the caller's cwd.
cd "$(dirname "$0")"

# Configuration
IMAGE_NAME="ofloo/pagersmtpd"
DEFAULT_TAG="latest"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER_NAME="multiarch-builder"
HAILO_BUILD=0

# Parse command line arguments
TAG="${1:-$DEFAULT_TAG}"
BUILD_ARGS=""
BUILD_NUMBER_FILE=".build_number"
BUILD_NUMBER=0
if [[ -f "$BUILD_NUMBER_FILE" ]]; then
    BUILD_NUMBER=$(<"$BUILD_NUMBER_FILE")
fi
BUILD_NUMBER=$((BUILD_NUMBER + 1))
printf '%s\n' "$BUILD_NUMBER" > "$BUILD_NUMBER_FILE"
BUILD_ARGS="--build-arg BUILD_NUMBER=$BUILD_NUMBER"

# Check if we should build for Hailo (ARM64 only)
if [[ "$TAG" == *"-hailo"* ]]; then
    echo "🔧 Hailo build detected - limiting to ARM64"
    PLATFORMS="linux/arm64"
    BUILD_ARGS+=" --build-arg HAILO_BUILD=true"
    HAILO_BUILD=1
fi

# Log configuration
echo "🏗️  Building image: ${IMAGE_NAME}:${TAG}"
echo "📦 Platforms: ${PLATFORMS}"
echo "🚀 Build args: ${BUILD_ARGS:-None}"

# Create multiarch builder if it doesn't exist
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    echo "🔧 Creating new builder: $BUILDER_NAME"
    docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
    docker buildx inspect --bootstrap
fi

# Ensure QEMU is installed for cross-arch emulation
echo "🔄 Setting up QEMU"
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# --- pager notification helpers ---------------------------------------------
notify_pager() {
    # $1 = status (running|succeeded|failed), $2 = message, $3 = logs
    # pager.sh --notify heeft geen logs-veld; logs gaan in de message (afgetopt).
    [ -x ./pager.sh ] || return 0
    local msg="$2"
    [ -n "$3" ] && msg="$msg
--- logs (tail) ---
$(printf '%s' "$3" | tail -n 20)"
    local dry=""
    [ "${PAGER_DRY_RUN:-0}" = "1" ] && dry="--dry-run"
    ./pager.sh --notify $dry \
        --session .pager_session \
        --project PagerSMPTd \
        --job-id "${IMAGE_NAME}:${TAG}-build${BUILD_NUMBER}" \
        --success-status "$1" --fail-status "$1" \
        --message "$msg" 2>/dev/null || true
}

notify_pager running "build started: ${IMAGE_NAME}:${TAG} build#${BUILD_NUMBER} platforms=${PLATFORMS}" ""
trap 'notify_pager failed "build FAILED: ${IMAGE_NAME}:${TAG} build#${BUILD_NUMBER}" "$(tail -n 30 .build_last.log 2>/dev/null || true)"' ERR
trap 'notify_pager failed "build interrupted: ${IMAGE_NAME}:${TAG} build#${BUILD_NUMBER}" ""' INT TERM

# Build and push; capture output for logs.
# Every build pushes BOTH the requested tag AND the version tag v<VERSION>,
# so a release always has a pinned image (e.g. ofloo/pagersmtpd:v0.1.0).
# A Hailo (arm64-only) build must NOT overwrite the multiarch version tag.
V="v$(tr -d ' \n' < VERSION 2>/dev/null || true)"
[[ "$V" == "v" ]] && V="v${TAG}"
if [[ "$HAILO_BUILD" == "1" ]]; then
    V="${V}-hailo"
fi
TAGS=("-t" "${IMAGE_NAME}:${TAG}")
if [[ "$V" != "v${TAG}" ]]; then
    TAGS+=("-t" "${IMAGE_NAME}:${V}")
fi
echo "🏷️  Image tags: ${IMAGE_NAME}:${TAG} ${IMAGE_NAME}:${V}"

BUILD_LOG=".build_last.log"
echo "🚀 Starting build..."
if docker buildx build \
    --platform "$PLATFORMS" \
    "${TAGS[@]}" \
    $BUILD_ARGS \
    --push \
    . 2>&1 | tee "$BUILD_LOG"; then
    BUILD_OK=1
else
    BUILD_OK=0
fi

if [[ "$BUILD_OK" != "1" ]]; then
    echo "❌ Build failed — no release commit, no tag."
    notify_pager failed "build FAILED: ${IMAGE_NAME}:${TAG} build#${BUILD_NUMBER}" "$(tail -n 50 "$BUILD_LOG" 2>/dev/null || true)"
    rm -f "$BUILD_LOG"
    exit 1
fi
rm -f "$BUILD_LOG"
trap - ERR INT TERM

echo "✅ Successfully built and pushed ${IMAGE_NAME}:${TAG} + ${IMAGE_NAME}:${V} for ${PLATFORMS}"

# --- release commit + tag: ONLY after a successful build --------------------
if git rev-parse --git-dir >/dev/null 2>&1; then
    if git rev-parse -q --verify "refs/tags/${V}" >/dev/null; then
        echo "🏷️  Tag ${V} already exists"
    else
        git add -A 2>/dev/null || true
        if ! git diff --cached --quiet; then
            git commit -q -m "release: ${V} build ${BUILD_NUMBER} (${IMAGE_NAME}:${TAG})" && echo "📝 Release commit ${V} build ${BUILD_NUMBER}"
        fi
        git tag -a "${V}" -m "${V} build ${BUILD_NUMBER}" && echo "🏷️  Tagged ${V} (build ${BUILD_NUMBER})"
    fi
fi

SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
notify_pager succeeded "build OK: ${IMAGE_NAME}:${TAG}+${V} build#${BUILD_NUMBER} commit=${SHA} platforms=${PLATFORMS}" "images pushed: ${IMAGE_NAME}:${TAG}, ${IMAGE_NAME}:${V}"
