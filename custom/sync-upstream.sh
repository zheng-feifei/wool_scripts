#!/usr/bin/env bash
# 同步上游规则 -> 重新生成 custom/loon/ 下的定制插件 -> 提交推送
# 用法：在仓库根目录执行  bash custom/sync-upstream.sh
#
# 关键点：本仓库 main 上的 Loon/ 等上游自有文件保持与上游一致，
# 所有定制内容只放在 custom/ 目录，因此与上游合并永远不会冲突。
set -euo pipefail

cd "$(dirname "$0")/.."

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/fmz200/wool_scripts.git
  echo "已添加 upstream 远程"
fi

echo "==> 拉取上游更新"
git fetch upstream main
git merge --no-edit upstream/main || {
  echo "合并出现冲突，请手动解决后重跑；不要点 GitHub 的 Discard commits"
  exit 1
}

echo "==> 重新应用自定义裁剪"
python custom/prune_ads.py

echo "==> 提交并推送"
git add custom/loon
if git diff --cached --quiet; then
  echo "上游规则没有带来变化，无需提交"
else
  git commit -m "chore: sync upstream and regenerate custom Loon plugins"
  git push origin main
fi

echo "完成。Loon 里的订阅地址不用改，仍是 custom/loon/ 下的 raw 链接。"
