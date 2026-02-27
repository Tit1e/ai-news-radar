const { createApp } = Vue;

createApp({
  data() {
    return {
      followItems: [],
      followGroups: [],
      momoyuItems: [],
      momoyuParsed: null,
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
    pickTitle(item) {
      return item.title || item.title_zh || item.title_en || "无标题";
    },
    hasBilingualTitle(item) {
      const zh = (item.title_zh || "").trim();
      const en = (item.title_en || "").trim();
      return Boolean(zh && en && zh !== en);
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
      this.loadError = "";
    } catch (err) {
      this.loadError = err && err.message ? err.message : "数据加载失败";
      this.followItems = [];
      this.followGroups = [];
      this.momoyuItems = [];
      this.momoyuParsed = null;
      this.generatedAt = null;
    }
  },
}).mount("#app");
