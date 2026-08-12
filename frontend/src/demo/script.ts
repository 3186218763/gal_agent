/**
 * 预制演示剧本 ——「咖啡馆疑云」
 *
 * 这是一个静态分支树,模拟 Agent 续写引擎的输出。
 * 真实产品中,这些 blocks / choices / endings 由后端实时生成,
 * 这里用预制数据让前端壳子独立可玩。
 */

export interface DemoBlock {
  kind: 'narration' | 'dialogue'
  text: string
  characterId?: string // narration 不需要;dialogue 指定说话者
}

export interface DemoChoice {
  id: string
  label: string
  goto: string
}

export interface DemoEnding {
  title: string
  tone: string
  summary: string
  cleared: boolean
}

export interface DemoNode {
  id: string
  scene: string // 背景场景 id
  blocks: DemoBlock[]
  choices?: DemoChoice[]
  goto?: string // 无选项时自动跳转
  ending?: DemoEnding
}

export interface DemoCharacter {
  id: string
  name: string
  color: string
}

export interface DemoScript {
  title: string
  subtitle: string
  characters: DemoCharacter[]
  scenes: { id: string; name: string }[]
  startNode: string
  nodes: Record<string, DemoNode>
}

export const demoScript: DemoScript = {
  title: '咖啡馆疑云',
  subtitle: '一段由 AI 驱动的故事,等待你的选择。',
  characters: [
    { id: 'protagonist', name: '悠真', color: '#94A3B8' },
    { id: 'alice', name: '艾丽丝', color: '#F08A8A' },
    { id: 'bob', name: '鲍勃', color: '#6BA3E8' },
    { id: 'mina', name: '美奈', color: '#7FC9A0' },
  ],
  scenes: [
    { id: 'cafe', name: '街角咖啡馆' },
    { id: 'evening', name: '黄昏 · 咖啡馆' },
  ],
  startNode: 'opening',

  nodes: {
    // ═══════════════ 开场 ═══════════════

    opening: {
      id: 'opening',
      scene: 'cafe',
      blocks: [
        { kind: 'narration', text: '周六下午。我推开街角咖啡馆的木门,门上的铃铛发出清脆的声响。' },
        { kind: 'narration', text: '朋友又迟到了。我挑了靠窗的位子坐下,百无聊赖地看着窗外人来人往。' },
        { kind: 'narration', text: '空气中弥漫着烘焙咖啡豆的焦香,店里很安静,只有磨豆机偶尔嗡嗡作响。' },
        { kind: 'narration', text: '就在这时,一个女孩急匆匆地推门走了进来。' },
        { kind: 'dialogue', characterId: 'alice', text: '......请问,有没有人看到一本笔记本？棕色的,大概这么大——' },
        { kind: 'narration', text: '她看起来很焦急,在咖啡馆里来回张望,手里还攥着一张皱巴巴的便条。' },
        { kind: 'narration', text: '直觉告诉我,她遇到了麻烦。' },
      ],
      choices: [
        { id: 'c1_help', label: '「你在找什么？需要帮忙吗？」', goto: 'alice_intro' },
        { id: 'c1_observe', label: '默默观察,先不插手', goto: 'observe' },
        { id: 'c1_ignore', label: '转头看向窗外,假装没注意', goto: 'ignore' },
      ],
    },

    // ═══════════════ 分支 A: 跟艾丽丝搭话 ═══════════════

    alice_intro: {
      id: 'alice_intro',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'alice', text: '啊......!你,你好。我在找一本笔记本,棕色的,硬壳的——' },
        { kind: 'narration', text: '她比划了一下大小。看起来确实容易被人拿错。' },
        { kind: 'dialogue', characterId: 'alice', text: '里面记了......一些很重要的东西。我记得明明放在桌上的,结果一转眼就不见了。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（这女孩,表情也太夸张了吧。不过......她说谎的时候眼神会飘。）' },
        { kind: 'narration', text: '就在这时,一个戴眼镜的男青年从隔壁桌站了起来。' },
        { kind: 'dialogue', characterId: 'bob', text: '笔记本？......你是不是在找这个？' },
        { kind: 'narration', text: '他手里拿着一本棕色笔记本。艾丽丝的眼睛瞬间亮了起来。' },
        { kind: 'dialogue', characterId: 'alice', text: '对！就是这个！太好了——' },
        { kind: 'dialogue', characterId: 'bob', text: '等一下。你是谁？为什么会有这种笔记？' },
      ],
      choices: [
        { id: 'c2_ally', label: '帮艾丽丝说话:「她是失主,笔记本当然是她的。」', goto: 'ally_alice' },
        { id: 'c2_listen', label: '沉默,先听鲍勃怎么说', goto: 'bob_warns' },
        { id: 'c2_question', label: '反问鲍勃:「那你又是怎么拿到的？」', goto: 'question_bob' },
      ],
    },

    ally_alice: {
      id: 'ally_alice',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'bob', text: '......哼。你们认识？' },
        { kind: 'dialogue', characterId: 'alice', text: '不,我们刚认识！但他说的没错,笔记本是我的——' },
        { kind: 'narration', text: '鲍勃沉默了几秒,然后把笔记本推了过来。' },
        { kind: 'dialogue', characterId: 'bob', text: '......拿去吧。不过我要提醒你,有些东西比笔记本本身更危险。' },
        { kind: 'dialogue', characterId: 'bob', text: '你不知道自己卷进了什么。' },
        { kind: 'dialogue', characterId: 'alice', text: '......谢谢你！真的谢谢！' },
        { kind: 'narration', text: '艾丽丝接过笔记本时,我注意到她的手在微微发抖。' },
      ],
      choices: [
        { id: 'c3_stay', label: '「你还好吗？要不要我陪你？」', goto: 'ally_ending' },
        { id: 'c3_bob', label: '「那个男的......好像知道什么。」', goto: 'investigate_bob' },
        { id: 'c3_quit', label: '「这件事到此为止吧。」', goto: 'withdraw' },
      ],
    },

    bob_warns: {
      id: 'bob_warns',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'bob', text: '我捡到这本笔记本的时候,翻了几页。你知道里面写了什么吗？' },
        { kind: 'dialogue', characterId: 'alice', text: '那是我私人的——' },
        { kind: 'dialogue', characterId: 'bob', text: '符号。密码。还有一些......不应该出现在普通笔记本里的东西。' },
        { kind: 'narration', text: '他的声音压得很低,但每个字都像钉子一样。' },
        { kind: 'dialogue', characterId: 'bob', text: '如果你真的丢了它,那可能不是坏事。有些真相,知道了反而更危险。' },
        { kind: 'dialogue', characterId: 'alice', text: '你怎么可以——！' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（......他在吓唬她？还是在保护她？）' },
      ],
      choices: [
        { id: 'c3b_safe', label: '「他说得有道理,你要小心。」', goto: 'safe_ending' },
        { id: 'c3b_truth', label: '「但我还是想知道真相。」', goto: 'truth_ending' },
      ],
    },

    question_bob: {
      id: 'question_bob',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'bob', text: '......' },
        { kind: 'narration', text: '他看了我一眼,推了推眼镜。' },
        { kind: 'dialogue', characterId: 'bob', text: '捡到的。就在那边的座位上。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（他的回答太快了。不像是临时编的,更像是......准备好了的。）' },
        { kind: 'dialogue', characterId: 'alice', text: '无论如何,请还给我。' },
        { kind: 'narration', text: '鲍勃没有立刻松手。他和艾丽丝之间,似乎有一场无声的角力。' },
      ],
      choices: [
        { id: 'c3c_take', label: '介入,拿过笔记本还给她', goto: 'ally_ending' },
        { id: 'c3c_push', label: '继续追问鲍勃的真实目的', goto: 'truth_ending' },
      ],
    },

    investigate_bob: {
      id: 'investigate_bob',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'alice', text: '那个......你说的是那个戴眼镜的人？' },
        { kind: 'dialogue', characterId: 'alice', text: '他刚才一直在角落看手机,但你一说......我总觉得他在偷听。' },
        { kind: 'narration', text: '我转头看去,那个位子已经空了。鲍勃不知道什么时候离开了。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（走这么快......是不想被记住,还是去做了什么？）' },
        { kind: 'dialogue', characterId: 'alice', text: '谢谢你帮我。我叫艾丽丝。你......愿意听我说说这本笔记的事吗？' },
        { kind: 'narration', text: '她犹豫了一下,像是做了什么重大决定。' },
        { kind: 'dialogue', characterId: 'alice', text: '这里面,记录了一个......组织的东西。我在调查它。' },
      ],
      goto: 'truth_ending',
    },

    // ═══════════════ 分支 B: 观察 ═══════════════

    observe: {
      id: 'observe',
      scene: 'cafe',
      blocks: [
        { kind: 'narration', text: '我决定先看看情况。女孩在咖啡馆里转了两圈,然后走向了吧台。' },
        { kind: 'dialogue', characterId: 'alice', text: '店长小姐,请问您有没有看到一本棕色的笔记本？我刚才就坐在这儿......' },
        { kind: 'narration', text: '吧台后面站着一个气质沉着的女性,她微微皱了皱眉。' },
        { kind: 'dialogue', characterId: 'mina', text: '棕色笔记本？......我不记得看到过。不过,如果您着急的话,可以先留个联系方式。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（店长......好像在回避什么。她的目光飘了一下。）' },
      ],
      choices: [
        { id: 'c2b_join', label: '走过去加入对话', goto: 'join_mina' },
        { id: 'c2b_watch', label: '继续旁观', goto: 'cafe_ending' },
      ],
    },

    join_mina: {
      id: 'join_mina',
      scene: 'cafe',
      blocks: [
        { kind: 'narration', text: '我站起身,走向了吧台。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '我好像看到过那本笔记本。棕色的,对吧？' },
        { kind: 'narration', text: '我其实没看到,但我想帮帮她。美奈的目光在我身上停了一秒。' },
        { kind: 'dialogue', characterId: 'mina', text: '......是吗。那希望那位客人能尽快找到。' },
        { kind: 'narration', text: '她擦着杯子的手顿了一下。然后,压低声音。' },
        { kind: 'dialogue', characterId: 'mina', text: '......如果我是你,我不会让太多人知道这件事。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（店长......果然知道些什么？）' },
        { kind: 'dialogue', characterId: 'alice', text: '真的吗？！您在哪里看到的？！' },
      ],
      choices: [
        { id: 'c3d_misdirect', label: '编一个方向,先帮艾丽丝支开', goto: 'mina_ending' },
        { id: 'c3d_ask', label: '直接问美奈:「您知道什么吧？」', goto: 'truth_ending' },
      ],
    },

    // ═══════════════ 分支 C: 忽略 ═══════════════

    ignore: {
      id: 'ignore',
      scene: 'cafe',
      blocks: [
        { kind: 'narration', text: '我别过头去,装作在看手机。但余光里,那个女孩似乎越来越急了。' },
        { kind: 'narration', text: '过了一会儿,一个戴眼镜的男人和她说上了话。看起来不太愉快。' },
        { kind: 'narration', text: '又过了几分钟,女孩几乎是跑着离开了咖啡馆。' },
        { kind: 'narration', text: '那个男人目送她离开,然后慢慢坐下,翻开了手中的一本书。不对——是笔记本。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（......那本笔记本,是那个女孩在找的东西吧？）' },
        { kind: 'narration', text: '他抬起头,和我的目光撞了个正着。他微微笑了一下。' },
        { kind: 'narration', text: '我赶紧移开了视线。' },
        { kind: 'narration', text: '......算了,不关我的事。' },
      ],
      goto: 'passerby_ending',
    },

    withdraw: {
      id: 'withdraw',
      scene: 'cafe',
      blocks: [
        { kind: 'dialogue', characterId: 'protagonist', text: '「这件事到此为止吧。我帮了你,但我不想惹上更多麻烦。」' },
        { kind: 'dialogue', characterId: 'alice', text: '......我明白了。还是谢谢你。' },
        { kind: 'narration', text: '艾丽丝抱着笔记本站起身。她走之前回头看了我一眼,像是想说什么,但最终什么也没说。' },
        { kind: 'narration', text: '门上的铃铛又响了一声。然后,店里恢复了安静。' },
      ],
      goto: 'safe_ending',
    },

    // ═══════════════ 结局 ═══════════════

    ally_ending: {
      id: 'ally_ending',
      scene: 'evening',
      blocks: [
        { kind: 'dialogue', characterId: 'alice', text: '...你真的愿意帮我吗？' },
        { kind: 'dialogue', characterId: 'protagonist', text: '「当然。你看起来需要帮手。」' },
        { kind: 'narration', text: '艾丽丝露出一个如释重负的微笑。窗外的夕阳把她的头发染成了金色。' },
        { kind: 'dialogue', characterId: 'alice', text: '我叫艾丽丝。这本笔记里......记录了一个叫「隐环」的组织的线索。' },
        { kind: 'dialogue', characterId: 'alice', text: '我一直在独自调查。但是......有一个伙伴的话,也许就不一样了。' },
        { kind: 'narration', text: '那一刻,我知道——这个故事才刚刚开始。' },
      ],
      ending: {
        title: '共同前线',
        tone: '你选择相信了艾丽丝。不管前方等着你们的是什么,至少不再是孤身一人。',
        summary: '艾丽丝后来告诉你笔记本的真正来历。关于那个叫「隐环」的组织......但这,是另一个故事了。',
        cleared: true,
      },
    },

    truth_ending: {
      id: 'truth_ending',
      scene: 'evening',
      blocks: [
        { kind: 'narration', text: '事情比想象的复杂。笔记本、艾丽丝、鲍勃......每个人都在隐藏什么。' },
        { kind: 'narration', text: '咖啡馆里的人渐渐散了。正当我以为一切要无疾而终时,美奈不知什么时候出现在我身后。' },
        { kind: 'dialogue', characterId: 'mina', text: '先生,咖啡馆要打烊了。' },
        { kind: 'dialogue', characterId: 'mina', text: '不过......有些事情,不如在这里做个了断。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '「......你知道笔记本在谁手里,对吧。」' },
        { kind: 'narration', text: '美奈沉默了一瞬,然后从围裙口袋里取出一个东西。棕色,硬壳。' },
        { kind: 'dialogue', characterId: 'mina', text: '笔记本一直在我这里。从一开始就是。' },
        { kind: 'dialogue', characterId: 'mina', text: '我在等——一个足够谨慎、又足够好奇的人。一个不会把事情闹大的人。' },
        { kind: 'dialogue', characterId: 'mina', text: '你通过了考验。关于隐环......你有权知道真相。' },
      ],
      ending: {
        title: '真相的重量',
        tone: '美奈最终告诉了你答案。笔记本从来不在艾丽丝或鲍勃手里——她在等待一个「足够谨慎」的人。',
        summary: '你通过了她的考验。关于隐环的调查,从今天起,你也成了其中一员。这个选择,没有回头路。',
        cleared: true,
      },
    },

    safe_ending: {
      id: 'safe_ending',
      scene: 'evening',
      blocks: [
        { kind: 'dialogue', characterId: 'alice', text: '......我知道了。谢谢你,虽然什么忙也没帮上。' },
        { kind: 'narration', text: '艾丽丝最终拿回了笔记本,但她的眼神里失去了之前的温度。' },
        { kind: 'narration', text: '鲍勃点了点头,像是在确认什么。然后他头也不回地离开了。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（这样就够了吧。有些事情,不知道比较好。）' },
        { kind: 'narration', text: '我喝完了杯中最后一口已经凉透的咖啡。窗外的天空变成了深蓝色。' },
      ],
      ending: {
        title: '安全距离',
        tone: '你选择了最稳妥的答案。没有人受伤,但也没有人得到什么。',
        summary: '后来你听说,那家咖啡馆换了一批店员。你再也没有见过那两个人。有些问题,你永远不会知道答案。',
        cleared: false,
      },
    },

    mina_ending: {
      id: 'mina_ending',
      scene: 'evening',
      blocks: [
        { kind: 'dialogue', characterId: 'protagonist', text: '「我觉得......后面那排书架那边有动静。」' },
        { kind: 'dialogue', characterId: 'alice', text: '谢谢您！我这就去看看！' },
        { kind: 'narration', text: '艾丽丝跑开了。美奈看着她的背影,轻轻叹了口气。' },
        { kind: 'dialogue', characterId: 'mina', text: '......你帮她撒了谎。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '「您真的不知道笔记本在哪？」' },
        { kind: 'dialogue', characterId: 'mina', text: '如果我说知道呢？' },
        { kind: 'narration', text: '她从吧台下面取出了那本棕色笔记本。原来她一直都知道。' },
        { kind: 'dialogue', characterId: 'mina', text: '有些东西,放在对的人手里才是安全的。......我觉得,你是对的人。' },
      ],
      ending: {
        title: '托付',
        tone: '美奈从吧台下面取出了那本棕色笔记本。原来她从一开始就知道一切。',
        summary: '她选择把笔记本——连同它承载的秘密——交给了你。这是信任,也是责任。请不要辜负它。',
        cleared: true,
      },
    },

    cafe_ending: {
      id: 'cafe_ending',
      scene: 'evening',
      blocks: [
        { kind: 'narration', text: '美奈帮艾丽丝留了联系方式,然后艾丽丝离开了。' },
        { kind: 'narration', text: '我坐在角落,喝完了最后一口咖啡。窗外,夕阳正在沉下去。' },
        { kind: 'narration', text: '美娜路过我的桌子时,放慢了脚步。' },
        { kind: 'dialogue', characterId: 'mina', text: '您刚才......其实看到了什么,对吧？' },
        { kind: 'dialogue', characterId: 'protagonist', text: '「......也许。」' },
        { kind: 'dialogue', characterId: 'mina', text: '没有看到,有时候是好事。好好享受您的下午吧。' },
        { kind: 'narration', text: '她微笑着走开了。我望着窗外的暮色,心想——也许她是对的。' },
      ],
      ending: {
        title: '安静下午',
        tone: '咖啡馆的下午,一如既往地平静。你知道有些事发生了,但你选择了不参与。',
        summary: '这个选择没有对错。只是......有些可能性,永远地关上了。',
        cleared: false,
      },
    },

    passerby_ending: {
      id: 'passerby_ending',
      scene: 'evening',
      blocks: [
        { kind: 'narration', text: '朋友终于来了。我们聊了些无关紧要的事,然后离开了咖啡馆。' },
        { kind: 'narration', text: '走出去的时候,我最后回头看了一眼。那个戴眼镜的男人还坐在那里,翻着那本棕色笔记本。' },
        { kind: 'narration', text: '他似乎感觉到了我的目光,抬起头,朝我举了举杯子。' },
        { kind: 'dialogue', characterId: 'protagonist', text: '（......算了。）' },
      ],
      ending: {
        title: '擦肩而过',
        tone: '那天的咖啡馆,有一个女孩在找东西。你没有在意。后来你再也没有见过她。',
        summary: '有些故事,从你转过头的那一刻起,就已经结束了。',
        cleared: false,
      },
    },
  },
}
