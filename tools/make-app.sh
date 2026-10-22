#!/usr/bin/env bash
# Package the amd64 and arm64 snaps of a ctrlX app into ONE .app file - on demand only: normal
# builds stay ./build-snap-amd64.sh / ./build-snap-arm64.sh and produce .snap files.
#
#   tools/make-app.sh <app-dir> [--build | --rebuild] [--sign <oem-cert> <key>]
#
# Takes <snap>_<version>_amd64.snap and <snap>_<version>_arm64.snap from the app folder (<version>
# from snap/snapcraft.yaml) and writes <app-dir>/<snap>_<version>.app: an uncompressed tar with
# public/snaps/<arch>/release/<snap>_<version>.snap - the architecture is in the path, not in the
# file name. Same layout as the SDK's scripts/app-signing-tool.sh, without signatures; the device
# installs the snap of its own architecture (gotchas #48, #49).
#   --build     first build the architectures that have no snap of this version (missing or
#               outdated), as full builds: ./build-snap-<arch>.sh --clean (a .app is handed over)
#   --rebuild   first build both architectures (--clean), also when snaps of this version exist
#   --sign C K  sign with the OEM App signing certificate C and its private key K: hands off to the
#               SDK's app-signing-tool.sh ($CTRLX_SDK, default ~/ctrlx-automation-sdk; picks up
#               <app-dir>/<snap>.privileges) and checks the result with its verify-apps.sh.
#               Unsigned .app files install only on devices that allow apps from unknown sources.
# Refuses when a snap is missing, the two versions differ or don't match snap/snapcraft.yaml, or
# a snap's meta/snap.yaml disagrees with its file name (name, version, architecture).
# A full build takes ~17 min per architecture in the emulated App Build Environment (gotchas #47):
# run --build / --rebuild in the background.
set -euo pipefail

ARCHS="amd64 arm64"
usage() { echo "usage: $0 <app-dir> [--build | --rebuild] [--sign <oem-cert> <key>]" >&2; exit 2; }
die() { echo "make-app: $*" >&2; exit 1; }

[ $# -ge 1 ] || usage
APP_DIR=${1%/}
shift
BUILD=none
CERT=
KEY=
while [ $# -gt 0 ]; do
  case "$1" in
    --build) BUILD=missing ;;
    --rebuild) BUILD=all ;;
    --sign) [ $# -ge 3 ] || usage; CERT=$2; KEY=$3; shift 2 ;;
    *) usage ;;
  esac
  shift
done
YAML=$APP_DIR/snap/snapcraft.yaml
[ -f "$YAML" ] || die "$YAML not found - not an app folder"

# value of a top-level key of a YAML file (quotes and trailing comment removed)
yaml_value() {
  sed -n "s/^$1:[[:space:]]*//p" "$2" | head -n 1 | sed "s/[[:space:]]#.*//; s/^[\"']//; s/[\"'][[:space:]]*\$//"
}
SNAP=$(yaml_value name "$YAML")
VERSION=$(yaml_value version "$YAML")
[ -n "$SNAP" ] && [ -n "$VERSION" ] || die "no name/version in $YAML"
snap_file() { echo "$APP_DIR/${SNAP}_${VERSION}_$1.snap"; }
mb() { awk -v b="$(stat -c %s "$1")" 'BEGIN { printf "%.1f MB", b / 1e6 }'; }

# versions of the <snap>_*_<arch>.snap files in the app folder, oldest first
versions_of() {
  local f v
  for f in "$APP_DIR/${SNAP}"_*_"$1".snap; do
    [ -e "$f" ] || continue
    v=${f##*/"${SNAP}"_}
    echo "${v%_"$1".snap}"
  done | sort -V | paste -sd' ' -
}

# architectures without a snap of $VERSION; explains why if a build is not requested
check_snaps() {
  local a found newest missing="" seen="" report=""
  NEED=""
  for a in $ARCHS; do
    found=$(versions_of "$a")
    if [ -f "$(snap_file "$a")" ]; then
      report+="  $a: $(basename "$(snap_file "$a")")"$'\n'
      seen+=" $a=$VERSION"
      continue
    fi
    NEED+=" $a"
    if [ -z "$found" ]; then
      missing+=" $a"
      report+="  $a: missing - no ${SNAP}_*_$a.snap"$'\n'
    else
      newest=${found##* }
      seen+=" $a=$newest"
      report+="  $a: no ${SNAP}_${VERSION}_$a.snap (found: $found)"$'\n'
    fi
  done
  NEED=${NEED# }
  [ -n "$NEED" ] || return 0
  if [ -n "$missing" ]; then
    WHY="missing architecture:$missing"
  elif [ "$(echo "$seen" | tr ' ' '\n' | sed '/^$/d; s/.*=//' | sort -u | wc -l)" -gt 1 ]; then
    WHY="versions differ:"
    for a in $seen; do
      WHY+=" ${a%=*} ${a#*=},"
    done
    WHY=${WHY%,}
  else
    WHY="the snaps are ${seen##*=}, but snap/snapcraft.yaml says $VERSION"
  fi
  REPORT=$report
}

build() {
  local script=$APP_DIR/build-snap-$1.sh
  [ -x "$script" ] || die "$script not found or not executable"
  echo "== make-app: full build $1: $script --clean (~17 min in the emulated build VM)"
  "$script" --clean
}

# name, version and architecture inside the snap must match its file name
check_snap_meta() {
  local f meta name ver archs
  f=$(snap_file "$1")
  if ! command -v unsquashfs >/dev/null; then
    echo "make-app: warning: unsquashfs not installed - contents of $(basename "$f") not checked" \
         "(sudo apt-get install squashfs-tools)" >&2
    return 0
  fi
  meta=$(unsquashfs -cat "$f" meta/snap.yaml 2>/dev/null) || die "$f: no meta/snap.yaml - not a snap?"
  name=$(printf '%s\n' "$meta" | sed -n 's/^name:[[:space:]]*//p' | head -n 1 | tr -d "'\"")
  ver=$(printf '%s\n' "$meta" | sed -n 's/^version:[[:space:]]*//p' | head -n 1 | tr -d "'\"")
  archs=$(printf '%s\n' "$meta" | sed -n '/^architectures:/,/^[^ -]/s/^[[:space:]]*-[[:space:]]*//p' | tr -d "'\"" | paste -sd' ' -)
  [ "$name" = "$SNAP" ] || die "$f: meta/snap.yaml has name '$name', expected $SNAP"
  [ "$ver" = "$VERSION" ] || die "$f: meta/snap.yaml has version '$ver', expected $VERSION (renamed file?)"
  case " $archs " in
    *" $1 "*) ;;
    *) die "$f: built for '${archs:-?}', not $1 (renamed file?)" ;;
  esac
}

NEED=""
WHY=""
REPORT=""
check_snaps
if [ "$BUILD" = all ]; then
  NEED=$ARCHS
elif [ -n "$NEED" ] && [ "$BUILD" = none ]; then
  printf 'make-app: cannot package %s %s (version from snap/snapcraft.yaml): %s\n%s' \
         "$SNAP" "$VERSION" "$WHY" "$REPORT" >&2
  printf 'Build first (full build, ~17 min each): cd %s%s\nor re-run with --build.\n' \
         "$APP_DIR" "$(for a in $NEED; do printf ' && ./build-snap-%s.sh --clean' "$a"; done)" >&2
  exit 1
fi
for a in $NEED; do
  build "$a"
done
for a in $ARCHS; do
  [ -f "$(snap_file "$a")" ] || die "$(snap_file "$a") is missing after the build"
  check_snap_meta "$a"
done

OUT=$APP_DIR/${SNAP}_${VERSION}.app
TMP=$(mktemp -d)
trap 'rm -rf "$TMP" "$OUT.part"' EXIT
if [ -z "$CERT" ]; then
  for a in $ARCHS; do
    mkdir -p "$TMP/stage/public/snaps/$a/release"
    cp "$(snap_file "$a")" "$TMP/stage/public/snaps/$a/release/${SNAP}_${VERSION}.snap"
  done
  tar --create --file="$TMP/out.app" --directory="$TMP/stage" \
      --sort=name --owner=0 --group=0 --numeric-owner public
  KIND="unsigned - installs only where apps from unknown sources are allowed"
else
  SDK=${CTRLX_SDK:-$HOME/ctrlx-automation-sdk}
  for f in "$SDK/scripts/app-signing-tool.sh" "$SDK/scripts/verify-apps.sh" "$CERT" "$KEY"; do
    [ -f "$f" ] || die "$f not found"
  done
  CERT=$(realpath "$CERT")
  KEY=$(realpath "$KEY")
  mkdir -p "$TMP/snaps" "$TMP/signed"
  for a in $ARCHS; do
    cp "$(snap_file "$a")" "$TMP/snaps/"
  done
  if [ -f "$APP_DIR/$SNAP.privileges" ]; then
    cp "$APP_DIR/$SNAP.privileges" "$TMP/snaps/"
  fi
  echo "== make-app: signing with $SDK/scripts/app-signing-tool.sh"
  bash "$SDK/scripts/app-signing-tool.sh" "$CERT" "$KEY" "$TMP/snaps" "$TMP/signed"
  [ -f "$TMP/signed/${SNAP}_${VERSION}.app" ] || die "the SDK signing tool wrote no ${SNAP}_${VERSION}.app"
  bash "$SDK/scripts/verify-apps.sh" "$CERT" "$TMP/signed" || die "signature verification failed"
  mv "$TMP/signed/${SNAP}_${VERSION}.app" "$TMP/out.app"
  KIND="signed with $(basename "$CERT")"
fi

# the archive holds exactly the expected files, and each snap is the one from the app folder
EXPECT=""
for a in $ARCHS; do
  EXPECT+="public/snaps/$a/release/${SNAP}_${VERSION}.snap"$'\n'
  [ -z "$CERT" ] || EXPECT+="public/snaps/$a/release/${SNAP}_${VERSION}.signature"$'\n'
done
GOT=$(tar --list --file="$TMP/out.app" | grep -v '/$' | sort -u)
[ "$GOT" = "$(printf '%s' "$EXPECT" | sort -u)" ] || die "unexpected archive content:"$'\n'"$GOT"
echo "== $(basename "$OUT"): $(mb "$TMP/out.app"), uncompressed tar, $KIND"
for a in $ARCHS; do
  member=public/snaps/$a/release/${SNAP}_${VERSION}.snap
  sum=$(tar --extract --to-stdout --file="$TMP/out.app" "$member" | sha256sum | cut -d' ' -f1)
  [ "$sum" = "$(sha256sum < "$(snap_file "$a")" | cut -d' ' -f1)" ] || die "$member differs from $(snap_file "$a")"
  echo "   $member  $(mb "$(snap_file "$a")")  sha256 ${sum:0:16}  <- $(basename "$(snap_file "$a")")"
done
cp "$TMP/out.app" "$OUT.part"
mv -f "$OUT.part" "$OUT"
echo "== written: $OUT  (sha256 $(sha256sum < "$OUT" | cut -d' ' -f1))"
echo "   install: tools/ctrlx.py [--device core] install $OUT --service"
