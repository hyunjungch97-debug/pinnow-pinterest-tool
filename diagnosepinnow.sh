#!/usr/bin/env bash
# Pinnow .app 아이콘 진단 스크립트 — 아무것도 바꾸지 않습니다 (read-only)
# 사용법: pinnow.spec이 있는 폴더에서
#   chmod +x diagnose-pinnow.sh && ./diagnose-pinnow.sh > diagnose.txt
# 그리고 diagnose.txt 파일 내용을 그대로 채팅에 붙여넣어주세요.

set +e
cd "$(dirname "$0")"

line() { printf '\n========== %s ==========\n' "$1"; }

line "0. 환경"
sw_vers 2>/dev/null
echo "PWD = $(pwd)"
which pyinstaller && pyinstaller --version
which iconutil
which sips
python3 --version 2>/dev/null
echo "OS arch: $(uname -m)"

line "1. 폴더 트리 (한 단계)"
ls -la

line "2. .spec 파일 목록"
ls *.spec 2>/dev/null
SPEC=$(ls pinnow.spec 2>/dev/null || ls *.spec 2>/dev/null | head -1)
echo "사용할 .spec: $SPEC"

line "3. .spec 전체 내용"
if [ -n "$SPEC" ] && [ -f "$SPEC" ]; then
  cat -n "$SPEC"
else
  echo "(없음)"
fi

line "4. .icns 후보"
ls -la *.icns 2>/dev/null || echo "(없음)"

line "5. .iconset 폴더 내용"
for d in pinnow.iconset pinnow_new.iconset; do
  if [ -d "$d" ]; then
    echo "--- $d ---"
    ls -la "$d"
  fi
done

line "6. .icns 사이즈 검증 (iconutil로 풀어서 보기)"
for f in pinnow.icns Pinnow.icns; do
  if [ -f "$f" ]; then
    echo "--- $f ---"
    rm -rf /tmp/_pinnow_check_iconset
    iconutil --convert iconset "$f" -o /tmp/_pinnow_check_iconset 2>&1
    if [ -d /tmp/_pinnow_check_iconset ]; then
      ls -la /tmp/_pinnow_check_iconset
      for png in /tmp/_pinnow_check_iconset/*.png; do
        sips -g pixelWidth -g pixelHeight "$png" 2>/dev/null | tail -2
      done
      rm -rf /tmp/_pinnow_check_iconset
    fi
  fi
done

line "7. make_icon.py 내용 (있으면)"
if [ -f make_icon.py ]; then
  cat -n make_icon.py
else
  echo "(없음)"
fi

line "8. dist/ 안 .app 검사"
APP=$(ls -d dist/*.app 2>/dev/null | head -1)
echo "감지된 .app: $APP"
if [ -n "$APP" ] && [ -d "$APP" ]; then
  echo "--- Contents 구조 ---"
  ls -la "$APP/Contents"
  echo "--- Contents/Resources (icns 위주) ---"
  ls -la "$APP/Contents/Resources" | grep -iE "\.icns|icon" || ls -la "$APP/Contents/Resources" | head -20

  PLIST="$APP/Contents/Info.plist"
  if [ -f "$PLIST" ]; then
    echo "--- Info.plist 주요 키 ---"
    for k in CFBundleName CFBundleDisplayName CFBundleIdentifier CFBundleIconFile CFBundleIconName NSHighResolutionCapable CFBundleExecutable; do
      v=$(/usr/libexec/PlistBuddy -c "Print :$k" "$PLIST" 2>/dev/null)
      printf "  %-28s = %s\n" "$k" "$v"
    done
  fi

  echo "--- .app 코드사인 상태 ---"
  codesign -dv "$APP" 2>&1 | head -10
  echo "--- .app 확장 속성 (quarantine 등) ---"
  xattr -l "$APP" 2>&1 | head -20
fi

line "9. 최근 PyInstaller 빌드 로그 흔적"
ls -la build/ 2>/dev/null | head -10
if [ -f "build/$SPEC.toc" ]; then
  echo "--- build/$SPEC.toc 처음 30줄 ---"
  head -30 "build/$SPEC.toc" 2>/dev/null
fi

line "10. Finder 아이콘 캐시 디렉토리 존재 여부"
for p in \
  "$HOME/Library/Caches/com.apple.iconservices.store" \
  "/Library/Caches/com.apple.iconservices.store"; do
  if [ -e "$p" ]; then
    echo "  존재: $p ($(du -sh "$p" 2>/dev/null | cut -f1))"
  else
    echo "  없음: $p"
  fi
done

line "끝"
echo ""
echo "→ 이 출력 전체를 복사해서 채팅창에 붙여넣어주세요."
