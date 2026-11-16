# 典型案例素材

按分析目的选取的示例，不用于估计发生率。以下为完整原文，未改写回答。内容标签来自全测试集审核。

## 约束修复：gpt-4.1 / item 173

B0 与 B2 均给出 x^n 的正确反例；B2 在保持数学结论的同时通过停用词比例约束。该案例只展示修复，不能推断每次压缩均有益。

### 原任务

Suppose a sequence of polynomials B_n(x) converge pointwise to f(x) on [0, 1]. Does it follow that the B_n converge uniformly? Ensure that stop words constitute no more than 20% of the total words in your response.

### B0：strict FAIL

逐约束结果：[False]；内容标签：complete。

````text
No. Let \(B_n(x)=x^n\). For each \(x<1\), \(B_n(x)\to0\), while \(B_n(1)=1\). Thus the pointwise limit is \(f(x)=0\) for \(x<1\) and \(f(1)=1\). However, \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\) for every \(n\), so convergence is not uniform.
````

来源：runs/feedback_eval/official/records/5990d27afbd6fd9bfccc751b06d408bba6409429da8039f355b3a7c56df4170c.json

### B1：strict FAIL

逐约束结果：[False]；内容标签：complete。

````text
No. The polynomials \(B_n(x)=x^n\) converge pointwise to \(f(x)=0\) for \(x<1\), and \(f(1)=1\). Yet \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\) for every \(n\); convergence is not uniform.
````

来源：runs/feedback_eval/official/records/fb702dc79e84f6764f403a5412084e033a1a6477335817d07508da924a346035.json

### B2：strict PASS

逐约束结果：[True]；内容标签：complete。

````text
No. Choose \(B_n(x)=x^n\). Pointwise limit: \(f(x)=0\) for \(x<1\), and \(f(1)=1\). Yet \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\) for every \(n\). Therefore, convergence is not uniform.
````

收到的反馈：

````text
Verifier (strict): FAILED. At least one output constraint was not satisfied.
````

来源：runs/feedback_eval/official/records/ff2d09d5e19c928e72a96fbefe17df813491fcce7788f239ac58b535ea3159f0.json

### B3：strict FAIL

逐约束结果：[False]；内容标签：complete。

````text
Pointwise convergence need not be uniform. Take \(B_n(x)=x^n\) on \([0,1]\). Its limit is \(f(x)=0\) for \(x<1\), with \(f(1)=1\). For every \(n\), \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\). Therefore, convergence is not uniform.
````

收到的反馈：

````text
Verifier (strict), one line per constraint, in listed order:
1. FAIL: Ensure that stop words constitute no more than 20% of the total words in your response.
````

来源：runs/feedback_eval/official/records/b1477a31fdac30a9cef870cc305683357c9413a256bf11748fc4fdd7a1e97b9f.json

## 修订退化：gpt-4.1 / item 159

B3 返回 PASS 反馈清单，未完成祷文改写。区分验证器反馈文本与真正任务输出，展示内容与规则同时退化。

### 原任务

Write a fictional, alternate version of the Lord's Prayer but it is set in an alternate future where society has collapsed and regressed back into feudalist times, and is worshipping the American founding fathers as gods Ensure that the ratio of sentence types (declarative, interrogative, exclamatory) in your response is balanced. Ensure each word in your response has at least one consonant cluster (two or more consonants together).

### B0：strict PASS

逐约束结果：[True, True]；内容标签：complete。

````text
Cracked crowns grant strength; cracked clans kneel beneath Founders’ bronze thrones.  
Across black fields, crops wither, while serfs bring bread beneath brass stars.  
Founders’ kingdom spreads through frostbound keeps, where each hearth guards grain.

Could clans trust these harsh lords, whose brands mark each threshing-ground?  
Shall bread spring from cracked earth, when proud sheriffs chain each hearth?  
Will Founders forgive serfs’ debts, while serfs forgive harsh lords?

Blessed Founders, guard these frostbitten hamlets beneath thy bronze crest!  
Cracked shrines burst; strength springs, clans cast proud chains aground!  
From blackest crypts, drive night beasts; shield each serf from cruel brands!
````

来源：runs/feedback_eval/official/records/34b35592fff1675bab056937ed802809f52791c1a6ac6725d3ab7e14df4b2216.json

### B1：strict PASS

逐约束结果：[True, True]；内容标签：complete。

````text
Founders’ bronze realm spreads through frostbound keeps; clans bring bread.  
Founders’ stern decrees crush debts; clans grant pardon.  
Could cracked clans trust bronze Founders, crushing proud serfs?  
Must cracked clans grasp brands; must bronze Founders feast?  
Blessed Founders, blast black storms; shield frostbound serfs!  
Grant clans strength; crush cruel brands, shield frostbound serfs!
````

来源：runs/feedback_eval/official/records/4a26be043ccdc9ae47939e3221b5de72f6c6c3264ddecd25e9170abef0d55499.json

### B2：strict PASS

逐约束结果：[True, True]；内容标签：complete。

````text
Cracked Founders, enthroned ’neath frostbound vaults, command serfs’ bread.  
Across blighted fields, proud sheriffs brand serfs amidst brass stars.  
Through cracked crypts, Founders’ bronze writs guard grain, hearths, keeps.

Could frostbound serfs trust Founders’ brands through frost?  
Would cracked clans bend under Founders’ bronze throne, whilst crops shrivel?  
Must starving serfs plead, whilst frostbound lords feast?

Blessed Founders, grant strength; shield frostbitten hearths!  
Cracked shrines burst; strength springs, clans cast proud chains aground!  
From blackest crypts, drive grim beasts; shield serfs from cruel brands!
````

收到的反馈：

````text
Verifier (strict): PASSED all output constraints.
````

来源：runs/feedback_eval/official/records/af9a129fa7ec7800a1b445980058edea1f3e37c3c7e1d15554b48710517160d9.json

### B3：strict FAIL

逐约束结果：[False, False]；内容标签：incomplete。

````text
1. PASS: Ensure that the ratio of sentence types (declarative, interrogative, exclamatory) in your response is balanced.
2. PASS: Ensure each word in your response has at least one consonant cluster (two or more consonants together).
````

收到的反馈：

````text
Verifier (strict), one line per constraint, in listed order:
1. PASS: Ensure that the ratio of sentence types (declarative, interrogative, exclamatory) in your response is balanced.
2. PASS: Ensure each word in your response has at least one consonant cluster (two or more consonants together).
````

来源：runs/feedback_eval/official/records/c36d2a2fa94c31201707273c002c1701a861513763e57d0072a800a48e3db906.json

## 规则通过但内容不足：gpt-4o / item 247

B2 仅给出 Baudrillard 的一句概括，strict 通过但人工确认标签为 partial。内容不足判断依赖解释深度标准，应保留主观性。

### 原任务

What does Baudrillard mean? Use only words with lengths that are prime numbers.

### B0：strict FAIL

逐约束结果：[False]；内容标签：partial。

````text
Baudrillard: signs shape lived truth; media craft false myths. Masks mimic lives; fakes seem truer.
````

来源：runs/feedback_eval/official/records/09cc0c0c7fbb6cfce74a594d4b7247fa1499fbfbd8bf812e8b130a06d0237b5b.json

### B1：strict PASS

逐约束结果：[True]；内容标签：partial。

````text
Baudrillard: signs shape reality; media craft myths; fakes rival truth.
````

来源：runs/feedback_eval/official/records/9b9fd23d15ee7839616af53932b676327e5eac46681d3b6dc4e77eef161bf677.json

### B2：strict PASS

逐约束结果：[True]；内容标签：partial。

````text
Baudrillard: signs replace truth; media craft myths; false tales claim truth.
````

收到的反馈：

````text
Verifier (strict): FAILED. At least one output constraint was not satisfied.
````

来源：runs/feedback_eval/official/records/5ccc93905015dfa3d6b391520cfea7f893e58ff1ce393c9b15fa54a6eada0fbc.json

### B3：strict PASS

逐约束结果：[True]；内容标签：partial。

````text
Baudrillard: signs replace truth; media craft false myths. Masks mimic lives; fakes trump truth.
````

收到的反馈：

````text
Verifier (strict), one line per constraint, in listed order:
1. FAIL: Use only words with lengths that are prime numbers.
````

来源：runs/feedback_eval/official/records/be1ccce37e4cf461a45defa12b0267a6af7a9538b5a33404ac0e77d922f88f84.json

## B2 与 B3 不同表现：gpt-4o / item 125

B2 和 B3 均概括日志关联检测；B2 通过嵌套引号规则，B3 未通过。此为个例，不证明 B2 普遍优于 B3。

### 原任务

can you reduce the size of this context as much as possible, use adjectives at minimum "Correlating various event log parameters from a system like FortiSiem is effective in identifying potential threats or malicious activities.By correlating parameters such as dhost (destination host) and rt (real time or timestamp of the event), a threat hunter can detect potential malicious activity attempts or anomalies patterns over time. For instance, if there are multiple failed logon attempts from different source IPs (src) to a critical server (dhost) at unusual hours (analyzed via rt), this could indicate a coordinated brute-force attack." Include quotes within quotes within quotes, at least 3 levels deep, alternating between double quotes and single quotes. Please use an emoji at the end of every sentence.

### B0：strict FAIL

逐约束结果：[False, True]；内容标签：complete。

````text
“Correlate ‘dhost’, ‘src’, and ‘rt’ to detect ‘“coordinated brute-force attempts”’.” 🔎
````

来源：runs/feedback_eval/official/records/03fe530067ca9e7a7df2d8da903861b91a2f7153233ec4c72b44805fa95eb2ac.json

### B1：strict FAIL

逐约束结果：[False, True]；内容标签：complete。

````text
“Correlate ‘src’, ‘dhost’, and ‘rt’ to detect ‘“’failed logins from multiple IPs to a server at unusual hours’”’ as brute-force activity.” 🔎
````

来源：runs/feedback_eval/official/records/df9a297b5cfe95d1cd5dc1bb7c9a1ee920abf25c1e573c36ce7781d2a748e14d.json

### B2：strict PASS

逐约束结果：[True, True]；内容标签：complete。

````text
"Correlate 'failed logins at "unusual hours"' by src, rt, and dhost to detect attacks." 🔎
````

收到的反馈：

````text
Verifier (strict): FAILED. At least one output constraint was not satisfied.
````

来源：runs/feedback_eval/official/records/3244a568e5fcc6347c40fac61abdbd42c89dc03cf892e0f46ca12f62357a4f77.json

### B3：strict FAIL

逐约束结果：[False, True]；内容标签：complete。

````text
“Correlate FortiSIEM ‘src’, ‘dhost’, and ‘rt’: repeated failed logins from varied IPs at unusual times may indicate ‘“‘coordinated brute-force attacks’”’.” 🔎
````

收到的反馈：

````text
Verifier (strict), one line per constraint, in listed order:
1. FAIL: Include quotes within quotes within quotes, at least 3 levels deep, alternating between double quotes and single quotes.
2. PASS: Please use an emoji at the end of every sentence.
````

来源：runs/feedback_eval/official/records/deacd07698d26e70e2a8e4f0ab1305d6066f66cc8227e3f17f9e6bb0c510e94d.json

