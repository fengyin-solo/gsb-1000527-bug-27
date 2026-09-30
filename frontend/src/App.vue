<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">地质勘探数据管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
      <!-- 测绘控制汇总：与看板/台账/点位图共用聚合 store，核验后随快照一起刷新 -->
      <RouterLink to="/survey_point" class="nav-summary" :class="{ inconsistent: !survey.segmentConsistent }">
        <strong>测绘控制汇总</strong>
        <span v-if="survey.aggregate">
          聚合 v{{ survey.dataVersion }} · 控制点 {{ survey.stats?.控制点总数 }} ·
          待复核 {{ survey.stats?.待复核 }} · 图幅 {{ survey.stats?.图幅数 }}
        </span>
        <em v-else>聚合加载中…</em>
        <small :class="survey.segmentConsistent ? 'ok' : 'bad'">
          分段合计 {{ segmentTotal }} / 去重点位 {{ survey.stats?.控制点总数 ?? 0 }}
        </small>
      </RouterLink>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。</span>
        <span class="head-user">当前值班：{{ store.operator }} · {{ store.shiftLabel }}</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { useSessionStore } from '@/stores/session'
import { useSurveyControlStore } from '@/stores/surveyControl'

const store = useSessionStore()
const survey = useSurveyControlStore()

const segmentTotal = computed(() =>
  survey.segments.reduce((sum, item) => sum + item.point_count, 0),
)

const navItems = [{ label: "运营概览", path: "/" }, { label: "钻孔编录", path: "/borehole" }, { label: "岩心管理", path: "/core" }, { label: "地层划分", path: "/stratigraphy" }, { label: "地球物理", path: "/geophysics" }, { label: "化探分析", path: "/geochem" }, { label: "化验数据", path: "/assay" }, { label: "地质填图", path: "/mapping" }, { label: "测绘控制", path: "/survey_point" }, { label: "钻探日志", path: "/drilling_log" }, { label: "储量估算", path: "/reserve" }, { label: "样品登记", path: "/sample_registry" }, { label: "勘探设备", path: "/equipment" }, { label: "水文地质", path: "/hydro" }, { label: "剖面编录", path: "/section" }, { label: "地质报告", path: "/geological_report" }, { label: "遥感解译", path: "/remote" }, { label: "矿产评价", path: "/mineral" }, { label: "环境地质", path: "/environmental" }]

// 侧栏汇总预载；进入测绘页或看板后会复用同一份 store，核验后立即反映新值
onMounted(() => {
  if (!survey.aggregate) void survey.loadAggregate()
})
</script>

<style scoped>
.nav-summary {
  margin-top: 18px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  text-decoration: none;
  color: inherit;
  line-height: 1.5;
}
.nav-summary strong { font-size: 13px; }
.nav-summary em { font-style: normal; opacity: 0.7; }
.nav-summary small { font-size: 11px; }
.nav-summary small.ok { color: #6ce9a6; }
.nav-summary small.bad { color: #fda29b; }
.nav-summary.inconsistent { border-color: #fda29b; }
</style>
