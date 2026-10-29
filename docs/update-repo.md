# 更新仓库

在资源机仓库根目录执行 `bash tools/update_repo.sh`。

脚本仅两行：配置 Git SSH（平台主进程的 `https_proxy`、`/workspace/.ssh/` 中的首个私钥、SSH 443），然后 `git pull --ff-only`。
适用于当前平台的无认证 HTTP 代理。需要 `netcat-openbsd`（`nc`）以及 `/workspace/.ssh/known_hosts` 中已核验的 GitHub 主机公钥；当前资源机均已配置。
实例重建后如缺少 nc，执行 `apt-get install -y netcat-openbsd`。该脚本只用于开发更新，封存评测期间不执行。
