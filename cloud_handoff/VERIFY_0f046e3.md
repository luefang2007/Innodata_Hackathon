# Linux 验证结果：`origin/main` @ `0f046e3`

- 验证时间：2026-11-14 约 13:05–13:20（美东）。
- 环境：云端 Linux 容器（Ubuntu 24.04，Python 3.12，15 GB 内存），Harbor 0.26.0，Docker 29.9.2，Compose v5.6.1。
- 对象：`submission/generated_tasks/`（5 个）和 `submission/generated_tasks_composite/`（5 个）。
- 没有改动 `factory/` 下的任何代码。

## 结论

| 检查 | 结果 |
|---|---|
| a1. 本地闸（`recheck`），10 个包 | 全部 PASS，8 道闸全过 |
| a2. 不用 Docker 的 oracle/nop，用包自带的 F2P/P2P 清单 | 10 个包 oracle 1.0、nop 0.0 |
| a3. Harbor oracle/nop（Docker） | 10 个包 oracle 1.0、nop 0.0，Exceptions 为 0。镜像与提交包有两处差别，见下 |
| b. 加了 `less` 后镜像里全量测试是否全绿 | **没法验证**：云端的 apt 仍被拦（`deb.debian.org` 返回 403） |

## 逐包结果

| 包 | 本地闸 | 变异 | F2P | P2P | Harbor oracle | Harbor nop |
|---|---|---|---|---|---|---|
| 0101 | PASS | 11/12 | 15 | 80 | 1.0（15/15，80/80） | 0.0（0/15，80/80） |
| 0102 | PASS | 12/12 | 14 | 80 | 1.0（14/14，80/80） | 0.0（0/14，80/80） |
| 0103 | PASS | 10/12 | 38 | 19 | 1.0（38/38，19/19） | 0.0（0/38，19/19） |
| 0104 | PASS | 11/12 | 27 | 80 | 1.0（27/27，80/80） | 0.0（0/27，80/80） |
| 0105 | PASS | 9/12 | 16 | 80 | 1.0（16/16，80/80） | 0.0（0/16，80/80） |
| 0201 | PASS | 12/12 | 56 | 160 | 1.0（56/56，160/160） | 0.0（0/56，160/160） |
| 0202 | PASS | 11/12 | 58 | 160 | 1.0（58/58，160/160） | 0.0（0/58，160/160） |
| 0203 | PASS | 11/12 | 67 | 160 | 1.0（67/67，160/160） | 0.0（0/67，160/160） |
| 0204 | PASS | 11/12 | 79 | 160 | 1.0（79/79，160/160） | 0.0（0/79，160/160） |
| 0205 | PASS | 11/12 | 69 | 160 | 1.0（69/69，160/160） | 0.0（0/69，160/160） |

括号里依次是通过的 F2P 和 P2P 数。不用 Docker 的 oracle/nop（a2）得到的数字与 Harbor 完全相同。`recheck` 10 个包共用 149 秒，峰值内存 280 MB。

## Harbor 那一项与提交包的差别

1. **基础镜像：** 用 `python:3.12-bookworm` 代替 `python:3.12-slim-bookworm`，并去掉了 apt 那一步，因为云端拦了 Debian 软件源。所以我构建的镜像里**没有** `less`，apt 那一行（含 `less`）没有被执行过。Dockerfile 的其余步骤原样执行。
2. **构建方式：** 镜像由 `docker build` 直接构建，Harbor 以预构建镜像模式运行（在临时副本的 `task.toml` 里填了 `docker_image`）。

因此仍需在一台网络正常的 Docker 机器上，用原样的 Dockerfile 跑一次。

## b 项怎么在别处验证

在任意一台能正常访问 Debian 软件源的 Docker 机器上，让 Harbor 构建一次任务镜像（跑一次 oracle 即可），然后用 `docker images` 查到镜像名，运行：

```bash
docker run --rm <任务镜像> bash -c 'which less && cd /app && python -m pytest tests -q -p no:cacheprovider --color=no | tail -3'
```

预期：打印出 `less` 的路径，全量测试 0 失败。我这边在没有 `less` 的镜像里看到的是 24 失败、2410 通过；在宿主机上有 `less` 时这 24 个用例全过。

## 观察

- **组合题的 P2P 是 160 个，`recheck` 自己重选的是 80 个。** 重选结果是包内清单的子集，包内 160 个在基线和 gold 下全部通过。`recheck` 不检查包自带的清单，a2 和 a3 检查了。
- **P2P 清单跨系统一致：** 5 个单功能包在 Linux 上重选的 P2P 与包内清单完全相同。
