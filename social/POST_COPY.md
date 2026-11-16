# Post copy for the poster

Images: `poster_<style>_en.jpg` for LinkedIn, `poster_<style>_zh.jpg` for WeChat Moments (2160 × 2700, 4:5).
Styles: `haze` (haze blue), `roman` (Pompeian red and gold), `gothic` (black, bone, crimson), `ink` (Chinese ink
wash), `lupi` (editorial data drawing), `cloth` (green cloth and gold), `stone` (ink and green on stone paper).
Edit freely; the numbers below are the same as on the poster.

## LinkedIn

One AI writes the exam. Another takes it first.

That is the idea behind the Solver-in-the-Loop Task Factory, which our team built at the Innodata Hackathon
in New York on November 14. It turns a real open-source codebase (pallets/click) into coding tasks for
evaluating AI models.

Claude Opus 5.5 writes each task: a feature request, a reference solution and the tests. Eight automated
checks decide whether the task is sound. Then Claude Haiku 5.5 attempts it three times. If the task is too
easy, it is made harder and measured again.

What we learned: a task can pass every check and still be easy. All five of our tasks were valid, and Haiku
solved each of them 3 times out of 3. One round in the loop took one task from 3/3 to 1/3.

In numbers: 5/5 tasks valid with zero hand edits. Harbor oracle 1.0, no-op 0.0. The generated tests caught
88% of the bugs we injected. $23.76 of model spend in total.

Next: make the other four tasks harder, and run the official benchmark.

Thank you to my teammates, and to Innodata for hosting.

#AIEvaluation #Benchmarks #LLM #Hackathon

## 朋友圈

一个 AI 出题，另一个 AI 先考。

11 月 14 日在纽约的 Innodata Hackathon，我们做了 Solver-in-the-Loop Task Factory：
从真实的开源代码库（pallets/click）自动生成评测 AI 编程能力的题目。

Opus 出题，8 项自动检查把关，Haiku 先做 3 遍。太简单就加深，再测一次。

最大的收获：题目"合格"不等于"够难"。5 道题全部通过检查，Haiku 却每道都 3 次全解出；
加深一轮后，其中一道降到 3 次解出 1 次。

几个数字：5 道题全部合格、零人工修改；生成的测试抓出了 88% 的注入 bug；模型总花费 $23.76。

感谢一起熬夜的队友们。
