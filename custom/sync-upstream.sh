#!/usr/bin/env bash
# 本地手动同步（应急 / 离线备用）。
# 日常同步已由 .github/workflows/sync-upstream.yml 自动完成，不需要执行本脚本。
# 用法：在仓库根目录执行  bash custom/sync-upstream.sh
#
# 与 Actions 保持一致的三重保护：合并前备份 custom/ -> 合并后恢复裁剪脚本
# -> 重新生成 -> 守卫校验 -> 校验通过才提交推送。校验失败则远程 main 不变。
set -euo pipefail

cd "$(dirname "$0")/.."

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream https://github.com/fmz200/wool_scripts.git
  echo "已添加 upstream 远程"
fi

echo "==> 备份 custom/ 定制资产"
rm -rf /tmp/custom-guard-local
mkdir -p /tmp/custom-guard-local
cp -a custom /tmp/custom-guard-local/custom

echo "==> 拉取并合并上游"
git fetch upstream main --no-tags
if ! git merge --no-edit -X theirs FETCH_HEAD; then
  echo "合并出现冲突，请手动解决后重跑；远程 main 未发生任何变化"
  exit 1
fi

echo "==> 恢复裁剪脚本（上游不得改写）"
mkdir -p custom
for f in prune_ads.py verify_custom.py sync-upstream.sh; do
  if ! cmp -s "/tmp/custom-guard-local/custom/$f" "custom/$f"; then
    echo "上游合并改写了 custom/$f，已从备份恢复"
    cp -a "/tmp/custom-guard-local/custom/$f" "custom/$f"
  fi
done

echo "==> 重新生成定制插件"
python custom/prune_ads.py

echo "==> 守卫校验（失败则不提交、不推送）"
python custom/verify_custom.py --baseline /tmp/custom-guard-local/custom/loon

echo "==> 提交并推送"
git add custom/prune_ads.py custom/verify_custom.py custom/sync-upstream.sh \
        custom/loon/blockAds.plugin \
        custom/loon/blockAdsV2.plugin \
        custom/loon/rejectAd.list
if ! git diff --cached --quiet; then
  git commit -m "chore: sync upstream and rebuild custom Loon plugins [skip ci]"
fi
AHEAD=$(git rev-list --count origin/main..HEAD)
if [ "$AHEAD" -gt 0 ]; then
  git push origin HEAD:main
  echo "已推送 ${AHEAD} 个提交"
else
  echo "上游无更新，定制内容无变化，无需推送"
fi

echo "完成。Loon 里的订阅地址不用改，仍是 custom/loon/ 下的 raw 链接。"
