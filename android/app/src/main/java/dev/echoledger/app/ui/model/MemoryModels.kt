package dev.echoledger.app.ui.model

data class TranscriptSegment(
    val id: String,
    val speaker: String,
    val time: String,
    val text: String
)

data class SubFragmentItem(
    val id: String,
    val title: String,
    val duration: String,
    val startTime: String = "",
    val endTime: String = "",
    val detailDuration: String = "",
    val fullDateLabel: String = "",
    val listDescription: String = "",
    val overviewSummary: String = "",
    val unrecognizedVoiceCount: Int = 1,
    val transcripts: List<TranscriptSegment> = emptyList()
)

data class MemoryEvent(
    val id: String,
    val dayNumber: Int, // 2 = Nov 2, 3 = Nov 3, 30 = Oct 30
    val dateIso: String = "", // e.g. "2026-11-04"
    val dayGroupLabel: String, // "昨天", "前天", "10月30日 周五"
    val datePrefixForPerson: String, // "昨天", "2026/11/02", "2026/10/30"
    val fullDateLabel: String,
    val startTime: String,
    val endTime: String,
    val durationLabel: String,
    val categoryWatermark: String, // "LEARNING", "MEETING", "WORK", "MEAL", "SOCIAL", ""
    val title: String,
    val speakers: List<String>,
    val unrecognizedVoiceCount: Int = 0,
    val summaryOverview: String,
    val subFragmentsStartTime: String = "",
    val subFragmentsEndTime: String = "",
    val subFragments: List<SubFragmentItem> = emptyList(),
    val transcripts: List<TranscriptSegment> = emptyList(),
    val detailedNoteMarkdown: String = ""
)

data class UnrecognizedVoiceClip(
    val id: String,
    val quote: String,
    val timestamp: String,
    val occurrences: Int,
    val sampleCount: Int
)

data class PersonVoiceProfile(
    val name: String,
    val dialogueCount: Int
)

data class AgentItem(
    val id: String,
    val name: String,
    val iconType: String
)

data class CalendarDayItem(
    val weekDayLabel: String,
    val dayNumber: Int,
    val dotCount: Int
)

object SampleMemoryData {
    val calendarDaysWeek1 = listOf(
        CalendarDayItem("四", 29, 0),
        CalendarDayItem("五", 30, 3),
        CalendarDayItem("六", 31, 1),
        CalendarDayItem("日", 1, 0),
        CalendarDayItem("一", 2, 3),
        CalendarDayItem("二", 3, 3),
        CalendarDayItem("三", 4, 0)
    )

    val initialPeople = listOf(
        PersonVoiceProfile("杨宇明", 5),
        PersonVoiceProfile("其它", 1),
        PersonVoiceProfile("老公", 3),
        PersonVoiceProfile("doge", 2),
        PersonVoiceProfile("周毅", 1),
        PersonVoiceProfile("纪禄平", 1),
        PersonVoiceProfile("戴波", 1)
    )

    val initialUnrecognizedClips = listOf(
        UnrecognizedVoiceClip(
            id = "clip_1",
            quote = "在哪里杀70级呐",
            timestamp = "2026年11月2日 19:19",
            occurrences = 2,
            sampleCount = 2
        ),
        UnrecognizedVoiceClip(
            id = "clip_2",
            quote = "宝付款成功交款，请支付解锁手机。",
            timestamp = "2026年11月2日 18:53",
            occurrences = 4,
            sampleCount = 2
        ),
        UnrecognizedVoiceClip(
            id = "clip_3",
            quote = "苹果，不然有两个那个吗？取餐。",
            timestamp = "2026年11月2日 18:28",
            occurrences = 4,
            sampleCount = 2
        ),
        UnrecognizedVoiceClip(
            id = "clip_4",
            quote = "我们吃一下。",
            timestamp = "2026年11月2日 18:28",
            occurrences = 2,
            sampleCount = 2
        ),
        UnrecognizedVoiceClip(
            id = "clip_5",
            quote = "好，我们就说把我打败，接下来是吧，我们屏幕上是怎么处理的呢？简单来说，我们对于屏幕上的倾向于不去看那些很重要的很有意义的这个数学，我们就把问题简单化。",
            timestamp = "2026年11月2日 18:04",
            occurrences = 160,
            sampleCount = 160
        ),
        UnrecognizedVoiceClip(
            id = "clip_6",
            quote = "力人群之类的，但是一个直营在梦中办完被学校叫过来给学生，然后还打一下比赛，虽然精力已经有点涣散了，但是呢，状态还是挺好的，所以说我希望同学们在这个年龄段可以多做一些中。",
            timestamp = "2026年11月2日 16:17",
            occurrences = 17,
            sampleCount = 17
        ),
        UnrecognizedVoiceClip(
            id = "clip_7",
            quote = "今天我将去见你，明天你是否会想起昨天你写的日记。",
            timestamp = "2026年11月2日 11:02",
            occurrences = 2,
            sampleCount = 1
        )
    )

    val agentList = listOf(
        AgentItem("claude", "Claude", "spark"),
        AgentItem("codex", "OpenAI Codex", "vortex"),
        AgentItem("hermes", "Hermes", "greek"),
        AgentItem("cursor", "Cursor", "cube"),
        AgentItem("antigravity", "Antigravity", "triangle"),
        AgentItem("openclaw", "OpenClaw", "crab"),
        AgentItem("grok", "Grok", "slash"),
        AgentItem("workbuddy", "WorkBuddy", "buddy"),
        AgentItem("deepseek", "DeepSeek", "whale"),
        AgentItem("more", "更多", "dots")
    )

    val events = listOf(
        // Nov 3 (Day 3 - 昨天)
        MemoryEvent(
            id = "ev_nov3_1",
            dayNumber = 3,
            dayGroupLabel = "昨天",
            datePrefixForPerson = "昨天",
            fullDateLabel = "2026年11月3日 周二 · 14:30 至 14:46 · 16m",
            startTime = "14:30",
            endTime = "14:46",
            durationLabel = "16m",
            categoryWatermark = "LEARNING",
            title = "概率统计期中复习课，说明六道大题题型并逐章梳理重点",
            speakers = listOf("杨宇明"),
            summaryOverview = "考前一周的复习说明：六道大题分简答与解答两类，另有一道选做题和若干填空题；逐章提示重点，强调全概率公式几乎每年必考，第三章最难也最重要。",
            transcripts = listOf(
                TranscriptSegment(
                    "t_301",
                    "杨宇明",
                    "14:30:35",
                    "因为我其实出过，但是这个出题应该说轮着来，所以每年也规定风格不一定，但题型是类似的，就是说也没有选择题，有没有填空题，剩下大概有六道大题，六道大题又分成大概两种类型，一种类型问题比较单一，大概有四道题，所谓的简答。"
                ),
                TranscriptSegment(
                    "t_302",
                    "杨宇明",
                    "14:31:03",
                    "还有一个要讲到解答问题，就是可能组合问题比较多，所以整体评估可能就有两道，所以一般来说讲是六道大题，然后大概有一个选择题，有若干的填空题，复习内容就前三章，因为同学可能有"
                )
            ),
            detailedNoteMarkdown = """
                概率统计期中复习 · 六道大题题型与重点梳理
                （2026-11-03 14:30–14:46，主讲：杨宇明）

                一、这次谈了什么
                1. 试卷题型结构
                • 无选择题，包含若干填空题 + 六道大题 + 一道选做题。
                • 六道大题分为两类：4道简答题（问题单一直接）与 2道综合解答题。

                2. 前三章核心考点
                • 第一章：全概率公式与贝叶斯公式几乎每年必考。
                • 第二章：一维随机变量分布律、分布函数与期望方差计算。
                • 第三章：多维随机变量联合分布、边缘分布与独立性判别（最难也最重要）。
            """.trimIndent()
        ),
        MemoryEvent(
            id = "ev_nov3_2",
            dayNumber = 3,
            dayGroupLabel = "昨天",
            datePrefixForPerson = "昨天",
            fullDateLabel = "2026年11月3日 周二 · 14:46 至 15:22 · 36m",
            startTime = "14:46",
            endTime = "15:22",
            durationLabel = "36m",
            categoryWatermark = "LEARNING",
            title = "概率统计课，总结方差性质并证明切比雪夫不等式",
            speakers = listOf("杨宇明"),
            summaryOverview = "推导指数分布方差为 1/λ²，总结常数倍、和的方差与零方差三条性质及标准化随机变量，重点证明切比雪夫不等式并给出 k 个标准差下的上界 1/k² 与等价形式。",
            transcripts = listOf(
                TranscriptSegment(
                    "t_303",
                    "杨宇明",
                    "14:48:12",
                    "我们接着来看方差的几条核心性质，首先常数倍的方差等于常数平方乘以原来的方差，如果是两个相互独立的随机变量之和，方差才等于各自方差之和。"
                ),
                TranscriptSegment(
                    "t_304",
                    "杨宇明",
                    "15:05:40",
                    "切比雪夫不等式给出了在未知具体分布形态时，利用期望和方差对事件概率做界定的通用工具，大家务必掌握截断积分放缩这一步证明。"
                )
            )
        ),
        MemoryEvent(
            id = "ev_nov3_3",
            dayNumber = 3,
            dayGroupLabel = "昨天",
            datePrefixForPerson = "昨天",
            fullDateLabel = "2026年11月3日 周二 · 15:22 至 16:06 · 43m",
            startTime = "15:22",
            endTime = "16:06",
            durationLabel = "43m",
            categoryWatermark = "LEARNING",
            title = "概率统计课，讲解协方差与相关系数的定义和性质",
            speakers = listOf("杨宇明"),
            summaryOverview = "证明线性关系下成正比例为1时相关系数必为±1，线性变换不改变|ρ|；用单位圆均匀分布说明不相关不等于独立，矩形均匀分布题的相关系数留作课下完成。",
            subFragmentsStartTime = "16:15",
            subFragmentsEndTime = "16:18",
            subFragments = listOf(
                SubFragmentItem(
                    id = "frag_nov3_1",
                    title = "又到两个快递要去取",
                    duration = "3m",
                    startTime = "16:15",
                    endTime = "16:18",
                    detailDuration = "3m",
                    fullDateLabel = "2026年11月3日 周二 · 16:15 至 16:18 · 3m",
                    listDescription = "简短提到刚收到两条取件码短信，打算下课顺路去驿站把两个快递取回来。",
                    overviewSummary = "一段简短的日常对话，提及收到两个快递通知并准备顺路去取件。"
                )
            ),
            transcripts = listOf(
                TranscriptSegment(
                    "t_305",
                    "杨宇明",
                    "15:25:19",
                    "相关系数衡量的是线性相关程度，如果两个变量不相关，只能说明没有线性关系，不代表它们相互独立，比如单位圆上的均匀分布就是经典反例。"
                )
            )
        ),

        // Nov 2 (Day 2 - 前天)
        MemoryEvent(
            id = "ev_nov2_1",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 08:33 至 10:05 · 1h 32m",
            startTime = "08:33",
            endTime = "10:05",
            durationLabel = "1h 32m",
            categoryWatermark = "LEARNING",
            title = "计算机组成原理课，整合多周期数据通路并穷举控制器控制信号",
            speakers = listOf("纪禄平"),
            summaryOverview = "控制信号可由硬布线与微程序两种方式产生，13组信号共18位；控制器比单周期多出当前时序周期这一输入，时序系统用五根互斥线表示FT、ET等周期并推演各指令的周期走向。",
            subFragmentsStartTime = "11:02",
            subFragmentsEndTime = "12:43",
            subFragments = listOf(
                SubFragmentItem(
                    id = "frag_nov2_1",
                    title = "电话与回忆的哼唱片段",
                    duration = "1m",
                    startTime = "11:02",
                    endTime = "11:03",
                    detailDuration = "0m",
                    fullDateLabel = "2026年11月2日 周一 · 11:02 至 11:03 · 0m",
                    listDescription = "转写置信度很低，只辨出零散词句，如“等电话在甄选，我们忘了喧腾自己，每天你相聚回忆”以及“今天我将去见你”，整体近似哼唱或歌词，没有形成可确认的对...",
                    overviewSummary = "一段识别度很低的语音，内容近似歌词哼唱，无法还原出明确主题。",
                    unrecognizedVoiceCount = 1,
                    transcripts = listOf(
                        TranscriptSegment(
                            "tf_1",
                            "待识别说话人",
                            "11:02:15",
                            "等电话在甄选，我们忘了喧腾自己，每天你相聚回忆……今天我将去见你。"
                        )
                    )
                ),
                SubFragmentItem(
                    id = "frag_nov2_2",
                    title = "哼唱《同桌的你》",
                    duration = "1m",
                    startTime = "11:05",
                    endTime = "11:07",
                    detailDuration = "1m",
                    fullDateLabel = "2026年11月2日 周一 · 11:05 至 11:07 · 1m",
                    listDescription = "转写置信度极低，整段近似断续哼唱或歌声，可辨认出的是《同桌的你》歌词片段，如“明天你是否会想起昨天你写的日记”“谁娶了多愁善感的你”“问我借半块橡皮”等，...",
                    overviewSummary = "一段断续哼唱《同桌的你》经典歌词的零散音频片段，无实质对话。",
                    unrecognizedVoiceCount = 1,
                    transcripts = listOf(
                        TranscriptSegment(
                            "tf_2",
                            "待识别说话人",
                            "11:05:20",
                            "明天你是否会想起，昨天你写的日记，明天你是否还惦记，曾经最爱哭的你……谁娶了多愁善感的你，问我借半块橡皮。"
                        )
                    )
                ),
                SubFragmentItem(
                    id = "frag_nov2_3",
                    title = "支付15元解锁手机",
                    duration = "23m",
                    startTime = "11:57",
                    endTime = "12:21",
                    detailDuration = "23m",
                    fullDateLabel = "2026年11月2日 周一 · 11:57 至 12:21 · 23m",
                    listDescription = "片段稀疏断续：先提到三月初，随后有人追问在急什么；另有一笔15元支付到账后解锁手机，回应没问题；末尾提到吃猪肉的肉。",
                    overviewSummary = "包含零星日常对话与15元到账提示音、解锁手机及午餐吃肉的简短交流。",
                    unrecognizedVoiceCount = 1,
                    transcripts = listOf(
                        TranscriptSegment(
                            "tf_3",
                            "待识别说话人",
                            "12:04:11",
                            "三月初那会儿吧，你在急什么？支付宝到账15元，请解锁手机。没问题，中午吃猪肉的肉。"
                        )
                    )
                ),
                SubFragmentItem(
                    id = "frag_nov2_4",
                    title = "电子院四号cm",
                    duration = "9m",
                    startTime = "12:34",
                    endTime = "12:43",
                    detailDuration = "9m",
                    fullDateLabel = "2026年11月2日 周一 · 12:34 至 12:43 · 9m",
                    listDescription = "录音片段零散、识别置信度低，只留下零星内容：提到“四号cm，这是电子院”；随后谈到时限问题，“你要拿七八个月底，还有人回不来”；另出现“第一年”“红”等零碎...",
                    overviewSummary = "围绕电子院四号及七八个月底人员返程时限的零散现场交谈。",
                    unrecognizedVoiceCount = 1,
                    transcripts = listOf(
                        TranscriptSegment(
                            "tf_4",
                            "待识别说话人",
                            "12:36:40",
                            "四号cm，这是电子院。你要拿七八个月底，还有人回不来，第一年红……"
                        )
                    )
                )
            ),
            transcripts = listOf(
                TranscriptSegment(
                    "t_201",
                    "纪禄平",
                    "08:33:21",
                    "我们取出来的是指令，把它放进去，因为这个有四个触发器，理论上这里就会有四次输出，是吧？但是呢，我们只要是在取指令的时候就有一个词叫，算过去了，它就有选择了，就是说我们也必须增加一个开关，就是这个IRdrive开关，这个开关，那同学们，这个地方为什么没有呢？它是数据，你写进去，但是你没有使用它的。"
                ),
                TranscriptSegment(
                    "t_202",
                    "纪禄平",
                    "08:33:50",
                    "分享它，朋友们想一想，如果我这里不加开关，那么第一个触发器取出来直接放进去以后，对不对？那么第二个触发器过来以后，这里我没断开"
                ),
                TranscriptSegment(
                    "t_203",
                    "纪禄平",
                    "08:34:19",
                    "们讲到这个，这个数据通路呢，它是针对的访存指令，你看第一个拍是取指令，怎么取呀，把这个存储指令，然后放到，放到IR寄存器，明白，放到IR寄存器，这是PC过来取过去，同时做PC加一。"
                ),
                TranscriptSegment(
                    "t_204",
                    "纪禄平",
                    "08:34:49",
                    "对不对，这两个就是我们这个取指令的一组通路，跟提升价值不是充足的，所以说他们是在设计当中，他们可以并行，这是第一拍取指令，那么取指令的话，指令就到IR当中去了，第二拍的话，那么就是根据指令，指令当中是有，R型指令的话高六位是操作码。"
                ),
                TranscriptSegment(
                    "t_205",
                    "纪禄平",
                    "08:35:19",
                    "刚因为是操作嘛，然后呢，第二连续三个寄存器的编号，那么这三个编号就送过来，过来呢？所以说第二拍的话，他就可以把A方子B方子的数"
                ),
                TranscriptSegment(
                    "t_206",
                    "纪禄平",
                    "08:40:49",
                    "就可以做到地址寻址方式，然后形成一个目标地址，形成一个目标地址以后，它要是去读取存储器的，这个我们可以加一个目标地址，要去读取存储器，读取存储器，读取存储器，对吧，读取存储器，那么读取存储器的这个地址有没有呢，有没有呢。"
                ),
                TranscriptSegment(
                    "t_207",
                    "纪禄平",
                    "08:41:19",
                    "看到没，这个读取这个，要去是吧，他要去呢五百几次是，我们想想这里有一个不甜的，他去五百几次，我们给他修改，修改过去的话，现在是吧，这样的话那我就可以把这个数据读出来，读出来以后放到哪里，这个时候的话读出来。"
                ),
                TranscriptSegment(
                    "t_208",
                    "纪禄平",
                    "08:41:49",
                    "从这里面读出来的，不能说IR当中去把IR的破坏了，因为IR内容还要使用，所以不能破坏，因此呢，我们想放到这里就必须得把开关的控制，就"
                ),
                TranscriptSegment(
                    "t_209",
                    "纪禄平",
                    "08:42:19",
                    "说这个读出来的数据它一定不会写入到IR去，因为我这个开关是要关上的，对吧？它不许过去，那么到MDR，这个数据就准备了，那么下一拍下一拍，那么到了下一拍这个。"
                )
            ),
            detailedNoteMarkdown = """
                计算机组成原理 · 多周期数据通路与控制信号
                （2026-11-02 08:33–10:05，主讲：纪禄平）

                一、这次谈了什么
                1. 补全多周期数据通路
                • 补上分支和跳转更新 PC 的通路；新增拼接器，把 26 位立即数左移两位后（28 位），与 PC+4 的高 4 位拼接，形成绝对跳转地址。
                • 完成 I 型访存指令通路（存储器数据先暂存到 Memory Data 缓冲寄存器，下一拍再写入 Rt），并检查通路能否覆盖 I 型运算指令。
                • 至此 11 条目标指令的数据通路全部完成。整合出六个多路选择器（寄存器堆写回源二选一、存储器地址二选一、存储器写数据二选一、ALU 的 A 端二选一、B 端四选一等），每个多路选择器引出控制信号，总共 13 组信号、18 个比特位。

                2. 控制信号穷举与状态转移
                • 穷举 11 条指令在各时钟周期（T0/T1/T2/T3）所需的控制信号；无关的控制信号位记为无所谓项（如 J 指令只需 2 个有效信号，其余全为无关项）。

                3. 控制器设计（第三个环节）
                • 控制信号有两种产生方式：硬布线逻辑与微程序。单周期用硬布线，多周期重点讲微程序。
                • 多周期控制器统输入输出与单周期相同，但多了一个参数——当前处于哪个时序周期，因为控制颗粒度细化到“周期”一级。
                • 时序系统：用五根互斥的线表示 FT、ET 等五个周期；下一次进入哪个周期，就让对应线为高，其余为低。

                二、值得记住的要点
                • 多周期的一拍：起点是一个存储元件，终点是另一个存储元件；所有信息必须在本拍写入保存。
                • 多周期中同一部件可被多次驱动，只要一个周期有效，因此把有看到它的拍做或（OR）。
                • 不要亢余：用不到的控制信号不必设置，否则增加控制信号负担。
                • 控制器统的两个输入条件：① 指令操作码（是什么指令）② 当前所处时序周期。

                三、结论与待办
                明确的结论：控制器必须同时依据指令操作码和当前时序周期，才能推断下一周期并产生对应控制信号——这是本次课的核心结论。

                待办：本次记录中没有出现带负责人和时间的具体任务；课堂仅提到后续还有定量分析要做（未指定负责人和时间）。故不另列事项。
            """.trimIndent()
        ),
        MemoryEvent(
            id = "ev_nov2_2",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 12:48 至 13:23 · 35m",
            startTime = "12:48",
            endTime = "13:23",
            durationLabel = "35m",
            categoryWatermark = "MEETING",
            title = "学生科研规划谈话，聊优化与大模型学习、周三组会安排",
            speakers = listOf("周毅", "doge"),
            unrecognizedVoiceCount = 3,
            summaryOverview = "确定把新生拉进周三下午组会旁听，十二月再给每人安排具体工作；有进展再投稿；建议直接读码、按兴趣摸清方向，理论路线转向实际工程并借助 GPT 推进。",
            subFragmentsStartTime = "13:24",
            subFragmentsEndTime = "14:32",
            subFragments = listOf(
                SubFragmentItem(
                    id = "frag_nov2_5",
                    title = "项目本质上应该这么搞",
                    duration = "14m",
                    startTime = "13:24",
                    endTime = "13:38",
                    detailDuration = "14m",
                    fullDateLabel = "2026年11月2日 周一 · 13:24 至 13:38 · 14m",
                    listDescription = "讨论工程落地细节，强调先跑通基线代码再做模块替换，项目本质上应该围绕实际数据闭环来推进。",
                    overviewSummary = "围绕工程落地与代码复现思路的简短讨论。"
                ),
                SubFragmentItem(
                    id = "frag_nov2_6",
                    title = "双床房报价127元",
                    duration = "3m",
                    startTime = "13:50",
                    endTime = "13:53",
                    detailDuration = "3m",
                    fullDateLabel = "2026年11月2日 周一 · 13:50 至 13:53 · 3m",
                    listDescription = "浏览酒店预订页面时随口核对双床房价格为127元一晚，确认离会场距离。",
                    overviewSummary = "核对出行住宿双床房127元报价的零散片段。"
                ),
                SubFragmentItem(
                    id = "frag_nov2_7",
                    title = "中间防守可能被打穿",
                    duration = "6m",
                    startTime = "14:26",
                    endTime = "14:32",
                    detailDuration = "6m",
                    fullDateLabel = "2026年11月2日 周一 · 14:26 至 14:32 · 6m",
                    listDescription = "课间闲聊战术站位，提到如果两边压太靠前，中间防守区域很容易被对方直接打穿。",
                    overviewSummary = "课间关于比赛防守站位与中路协防的零散对话。"
                )
            )
        ),
        MemoryEvent(
            id = "ev_nov2_3",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 14:44 至 15:07 · 23m",
            startTime = "14:44",
            endTime = "15:07",
            durationLabel = "23m",
            categoryWatermark = "WORK",
            title = "后期盘技术沟通，要求不出现关闭状态并就近找人接盘",
            speakers = listOf("其它"),
            unrecognizedVoiceCount = 2,
            summaryOverview = "要求黑盘绝不出现关闭状态，就近拉位接盘，并讲解平锤倾斜与远大会麦高卡、用无限弹链开防守阵容队伍、正反手握法只靠手腕提高高度与出手时机。"
        ),
        MemoryEvent(
            id = "ev_nov2_4",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 15:27 至 15:55 · 28m",
            startTime = "15:27",
            endTime = "15:55",
            durationLabel = "28m",
            categoryWatermark = "WORK",
            title = "飞盘训练课，凑够六人开打比赛并安排其他人跑三圈",
            speakers = emptyList(),
            unrecognizedVoiceCount = 8,
            summaryOverview = "凑够六人即可开打比赛，没上场的同学被要求去跑三圈；穿插谈论最大心率为 220 减年龄、60%–70% 属有氧、70%–80% 刺激心肺，140 左右属中等强度，每周一两次。"
        ),
        MemoryEvent(
            id = "ev_nov2_5",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 16:16 至 17:55 · 1h 38m",
            startTime = "16:16",
            endTime = "17:55",
            durationLabel = "1h 38m",
            categoryWatermark = "LEARNING",
            title = "大学物理课，从简谐振动合成讲到光栅干涉与相控阵雷达",
            speakers = emptyList(),
            unrecognizedVoiceCount = 1,
            summaryOverview = "推导拍频为 ν2−ν1（ω2−ω1 除以 2π），可用已知频率测定未知高频；李萨如图形由同位差和频率比决定；将干涉与多波源合成对比，关键合成振动的方向与频率。",
            subFragmentsStartTime = "18:01",
            subFragmentsEndTime = "18:26",
            subFragments = listOf(
                SubFragmentItem(
                    id = "frag_nov2_8",
                    title = "豆米火锅人均二十几",
                    duration = "25m",
                    startTime = "18:01",
                    endTime = "18:26",
                    detailDuration = "25m",
                    fullDateLabel = "2026年11月2日 周一 · 18:01 至 18:26 · 25m",
                    listDescription = "下课后商量晚饭去哪吃，提到附近一家豆米火锅人均只要二十几块钱，决定过去试试。",
                    overviewSummary = "晚餐前商量去吃人均二十几元的豆米火锅。"
                )
            )
        ),
        MemoryEvent(
            id = "ev_nov2_6",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 18:27 至 18:52 · 24m",
            startTime = "18:27",
            endTime = "18:52",
            durationLabel = "24m",
            categoryWatermark = "MEAL",
            title = "豆米火锅晚餐，边吃边拌嘴聊扣分和付款解锁",
            speakers = listOf("老公", "doge"),
            unrecognizedVoiceCount = 1,
            summaryOverview = "确认豆米有锅底，点豆苗白水菜类和折耳根，人均二十几；拌嘴只敢扣三分、追问早知道为何不戒成 PWA，并打算把溢出的 int 换成 short，最后解锁手机付款成功。"
        ),
        MemoryEvent(
            id = "ev_nov2_7",
            dayNumber = 2,
            dayGroupLabel = "前天",
            datePrefixForPerson = "2026/11/02",
            fullDateLabel = "2026年11月2日 周一 · 19:05 至 19:16 · 10m",
            startTime = "19:05",
            endTime = "19:16",
            durationLabel = "10m",
            categoryWatermark = "SOCIAL",
            title = "组队打游戏，分头找70级怪的击杀位置",
            speakers = listOf("老公"),
            unrecognizedVoiceCount = 1,
            summaryOverview = "两人分头去找70级怪的击杀点却始终没找到，感慨地图上东西太多，最后只冒出一句121。"
        ),

        // Oct 30 (Day 30 - 10月30日 周五)
        MemoryEvent(
            id = "ev_oct30_1",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 15:10 至 15:24 · 13m",
            startTime = "15:10",
            endTime = "15:24",
            durationLabel = "13m",
            categoryWatermark = "SOCIAL",
            title = "河道边拍照，沿河取景并顺带提到共享单车",
            speakers = listOf("老公"),
            summaryOverview = "取景，中间为零散现场短句，末尾提到共享单车，没有明确安排。"
        ),
        MemoryEvent(
            id = "ev_oct30_2",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 12:49 至 14:05 · 1h 16m",
            startTime = "12:49",
            endTime = "14:05",
            durationLabel = "1h 16m",
            categoryWatermark = "SOCIAL",
            title = "午饭后出门闲逛，聊花没结种子又在王者里玩斗地主",
            speakers = emptyList(),
            summaryOverview = "因昨夜酣睡和争吵后转去玩游戏，用鲁班六号在死亡状态下移动打出灼烧伤害反杀满血法师，期间一直喊卡、没血。"
        ),
        MemoryEvent(
            id = "ev_oct30_3",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 12:23 至 12:47 · 24m",
            startTime = "12:23",
            endTime = "12:47",
            durationLabel = "24m",
            categoryWatermark = "MEAL",
            title = "点炸鸡，坚持要一整只不要半边并到店自取打包",
            speakers = emptyList(),
            summaryOverview = "确定走到店下单、不加土豆泥，坚持要一整只鸡而非半边，随后到店自取并打包带走。"
        ),
        MemoryEvent(
            id = "ev_oct30_4",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 10:20 至 11:57 · 1h 36m",
            startTime = "10:20",
            endTime = "11:57",
            durationLabel = "1h 36m",
            categoryWatermark = "LEARNING",
            title = "概率统计课，用无意识统计学家法则推导期望与性质",
            speakers = listOf("杨宇明"),
            summaryOverview = "把方差定义为 X 减期望的平方的期望，伯努利分布 p(1-p) 在 p=1/2 时最大方差四分之一，泊松方差为 λ，独立时和的方差等于方差和，据此得二项方差 np(1-p)、正态方差 σ²。"
        ),
        MemoryEvent(
            id = "ev_oct30_5",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 10:14 至 10:20 · 6m",
            startTime = "10:14",
            endTime = "10:20",
            durationLabel = "6m",
            categoryWatermark = "LEARNING",
            title = "课堂学习提醒，AI 对成绩的影响和刷题误区",
            speakers = listOf("杨宇明"),
            summaryOverview = "引用研究称滥用 AI 的学生期末成绩可能降低 25%–30%，建议期中后验证影响；强调别只刷题型，要串联知识框架理解内容。"
        ),
        MemoryEvent(
            id = "ev_oct30_6",
            dayNumber = 30,
            dayGroupLabel = "10月30日 周五",
            datePrefixForPerson = "2026/10/30",
            fullDateLabel = "2026年10月30日 周五 · 09:14 至 10:11 · 56m",
            startTime = "09:14",
            endTime = "10:11",
            durationLabel = "56m",
            categoryWatermark = "LEARNING",
            title = "数据结构课，推导二叉树空指针数并机理先中后序遍历",
            speakers = listOf("戴波"),
            summaryOverview = "写出先序递归遍历函数 preOrder，指出中序、后序只是访问根的位置不同；分析递归回退过程得知用栈实现非递归，出栈是找右子树，非递归代码留作课后完成。"
        )
    )
}

fun SubFragmentItem.toMemoryEvent(dayNumber: Int = 2): MemoryEvent {
    return MemoryEvent(
        id = id,
        dayNumber = dayNumber,
        dayGroupLabel = "前天",
        datePrefixForPerson = "2026/11/02",
        fullDateLabel = fullDateLabel.ifEmpty { "2026年11月2日 周一 · $startTime 至 $endTime · $detailDuration" },
        startTime = startTime,
        endTime = endTime,
        durationLabel = detailDuration.ifEmpty { duration },
        categoryWatermark = "",
        title = title,
        speakers = emptyList(),
        unrecognizedVoiceCount = unrecognizedVoiceCount,
        summaryOverview = overviewSummary.ifEmpty { listDescription },
        transcripts = transcripts
    )
}

fun jsonToMemoryEvent(json: org.json.JSONObject): MemoryEvent {
    val startMs = json.optLong("start_ms", System.currentTimeMillis())
    val endMs = json.optLong("end_ms", startMs + 60000)
    val zone = java.time.ZoneId.systemDefault()
    val startDateTime = java.time.Instant.ofEpochMilli(startMs).atZone(zone)
    val endDateTime = java.time.Instant.ofEpochMilli(endMs).atZone(zone)
    val dayNumber = startDateTime.dayOfMonth
    val timeFormatter = java.time.format.DateTimeFormatter.ofPattern("HH:mm")
    val startTime = startDateTime.format(timeFormatter)
    val endTime = endDateTime.format(timeFormatter)

    val durationMinutes = ((endMs - startMs) / 60000).coerceAtLeast(1)
    val durationLabel = if (durationMinutes >= 60) {
        val h = durationMinutes / 60
        val m = durationMinutes % 60
        if (m > 0) "${h}h ${m}m" else "${h}h"
    } else {
        "${durationMinutes}m"
    }

    val today = java.time.LocalDate.now(zone)
    val eventDate = startDateTime.toLocalDate()
    val daysDiff = java.time.temporal.ChronoUnit.DAYS.between(eventDate, today)
    val dayGroupLabel = when (daysDiff) {
        0L -> "今天"
        1L -> "昨天"
        2L -> "前天"
        else -> startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("MM月dd日 E"))
    }
    val datePrefix = when (daysDiff) {
        0L -> "今天"
        1L -> "昨天"
        2L -> "前天"
        else -> startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("yyyy/MM/dd"))
    }
    val fullDateLabel = "${startDateTime.format(java.time.format.DateTimeFormatter.ofPattern("yyyy年MM月dd日 E"))} · $startTime 至 $endTime · $durationLabel"

    val speakersList = mutableListOf<String>()
    val speakerNamesArr = json.optJSONArray("speaker_names")
    if (speakerNamesArr != null) {
        for (i in 0 until speakerNamesArr.length()) {
            val name = speakerNamesArr.optString(i)
            if (name.isNotBlank()) speakersList.add(name)
        }
    }

    val typeId = json.optString("type_id", "").uppercase()
    val watermark = when {
        typeId.contains("MEET") -> "MEETING"
        typeId.contains("LEARN") -> "LEARNING"
        typeId.contains("WORK") -> "WORK"
        typeId.contains("MEAL") -> "MEAL"
        typeId.contains("SOCIAL") -> "SOCIAL"
        else -> typeId.take(8)
    }

    val segmentsList = mutableListOf<TranscriptSegment>()
    val segArr = json.optJSONArray("segments")
    if (segArr != null) {
        for (i in 0 until segArr.length()) {
            val seg = segArr.optJSONObject(i) ?: continue
            val segStartMs = seg.optLong("start_ms", startMs)
            val segTime = java.time.Instant.ofEpochMilli(segStartMs).atZone(zone).format(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss"))
            segmentsList.add(
                TranscriptSegment(
                    id = seg.optString("id", "seg_$i"),
                    speaker = seg.optString("speaker_name").ifBlank { "待确认发言人" },
                    time = segTime,
                    text = seg.optString("text")
                )
            )
        }
    }

    val summary = json.optString("summary").ifBlank {
        json.optString("overview").ifBlank {
            json.optString("title")
        }
    }
    val detailedNote = json.optString("detailed_notes").ifBlank {
        json.optString("note")
    }

    return MemoryEvent(
        id = json.optString("id"),
        dayNumber = dayNumber,
        dateIso = eventDate.toString(),
        dayGroupLabel = dayGroupLabel,
        datePrefixForPerson = datePrefix,
        fullDateLabel = fullDateLabel,
        startTime = startTime,
        endTime = endTime,
        durationLabel = durationLabel,
        categoryWatermark = watermark,
        title = json.optString("title", "未命名记录"),
        speakers = speakersList,
        unrecognizedVoiceCount = json.optInt("unassigned_labels", 0),
        summaryOverview = summary,
        transcripts = segmentsList,
        detailedNoteMarkdown = detailedNote
    )
}

fun jsonToPersonVoiceProfile(json: org.json.JSONObject): PersonVoiceProfile {
    return PersonVoiceProfile(
        name = json.optString("name", "未命名"),
        dialogueCount = json.optInt("voiceprint_count", json.optInt("dialogue_count", 1)).coerceAtLeast(1)
    )
}

