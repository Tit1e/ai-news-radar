const { createApp } = Vue;

createApp({
  data() {
    return {
      followItems: [],
      followGroups: [],
      momoyuItems: [],
      momoyuParsed: null,
      selectedFollowGroupKey: "",
      selectedMomoyuGroupKey: "",
      generatedAt: null,
      loadError: "",
    };
  },
  computed: {
    momoyuSections() {
      const sections = this.momoyuParsed && Array.isArray(this.momoyuParsed.sections)
        ? this.momoyuParsed.sections
        : [];
      return sections;
    },
    hasMomoyuSections() {
      return this.momoyuSections.length > 0;
    },
    momoyuSplitGroups() {
      return this.momoyuSections.map((sec, index) => ({
        key: `momoyu-${index}`,
        name: sec.section || "未命名榜单",
        entries: Array.isArray(sec.entries) ? sec.entries : [],
      }));
    },
    activeMomoyuGroup() {
      if (!this.momoyuSplitGroups.length) return null;
      return (
        this.momoyuSplitGroups.find((group) => group.key === this.selectedMomoyuGroupKey) ||
        this.momoyuSplitGroups[0]
      );
    },
    followSplitGroups() {
      return (this.followGroups || []).map((group, index) => ({
        key: `${String(group.subscription_url || group.source || "follow")}::${index}`,
        name: group.source || "未命名订阅",
        items: Array.isArray(group.items) ? group.items : [],
      }));
    },
    activeFollowGroup() {
      if (!this.followSplitGroups.length) return null;
      return (
        this.followSplitGroups.find((group) => group.key === this.selectedFollowGroupKey) ||
        this.followSplitGroups[0]
      );
    },
    followCountText() {
      if (this.followGroups.length > 0) {
        const shownCount = this.followGroups.reduce(
          (acc, group) => acc + ((group.items || []).length || 0),
          0,
        );
        return `${this.fmtNumber(this.followGroups.length)} 个订阅 · ${this.fmtNumber(shownCount)} 条`;
      }
      return `${this.fmtNumber(this.followItems.length)} 条`;
    },
    momoyuCountText() {
      if (this.hasMomoyuSections) {
        return `${this.fmtNumber(this.momoyuSections.length)} 个榜单`;
      }
      return `${this.fmtNumber(this.momoyuItems.length)} 条`;
    },
    updatedLabel() {
      if (this.loadError) return "数据加载失败";
      return `更新时间：${this.fmtTime(this.generatedAt)}`;
    },
    followEmptyText() {
      return this.loadError || "暂无 follow.opml 数据。";
    },
    momoyuEmptyText() {
      return this.loadError || "暂无 momoyu RSS 数据。";
    },
  },
  methods: {
    fmtNumber(n) {
      return new Intl.NumberFormat("zh-CN").format(n || 0);
    },
    fmtTime(iso) {
      if (!iso) return "时间未知";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "时间未知";
      return new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(d);
    },
    fmtDate(iso) {
      if (!iso) return "日期未知";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "日期未知";
      return new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
      }).format(d);
    },
    pickTitle(item) {
      return item.title || item.title_zh || item.title_en || "无标题";
    },
    hasBilingualTitle(item) {
      const zh = (item.title_zh || "").trim();
      const en = (item.title_en || "").trim();
      return Boolean(zh && en && zh !== en);
    },
    syncSelectedGroups() {
      if (this.momoyuSplitGroups.length) {
        const exists = this.momoyuSplitGroups.some((group) => group.key === this.selectedMomoyuGroupKey);
        if (!exists) this.selectedMomoyuGroupKey = this.momoyuSplitGroups[0].key;
      } else {
        this.selectedMomoyuGroupKey = "";
      }

      if (this.followSplitGroups.length) {
        const exists = this.followSplitGroups.some((group) => group.key === this.selectedFollowGroupKey);
        if (!exists) this.selectedFollowGroupKey = this.followSplitGroups[0].key;
      } else {
        this.selectedFollowGroupKey = "";
      }
    },
    async loadNewsData() {
      const res = await fetch(`./data/latest-24h.json?t=${Date.now()}`);
      if (!res.ok) throw new Error(`加载 latest-24h.json 失败: ${res.status}`);
      return res.json();
    },
  },
  async mounted() {
    try {
      const payload = await this.loadNewsData();
      this.followItems = payload.follow_opml_items || [];
      this.followGroups = payload.follow_opml_groups || [];
      this.momoyuItems = payload.momoyu_items || [];
      this.momoyuParsed = payload.momoyu_parsed || null;
      this.generatedAt = payload.generated_at || null;
      this.syncSelectedGroups();
      this.loadError = "";
    } catch (err) {
      this.loadError = err && err.message ? err.message : "数据加载失败";
      this.followItems = [];
      this.followGroups = [];
      this.momoyuItems = [];
      this.momoyuParsed = null;
      this.selectedFollowGroupKey = "";
      this.selectedMomoyuGroupKey = "";
      this.generatedAt = null;
    }
  },
}).mount("#app");
