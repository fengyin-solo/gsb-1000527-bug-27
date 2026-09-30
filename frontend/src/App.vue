<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">地质勘探数据管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
      <section class="side-summary" data-module="survey_control_summary">
        <header class="side-summary-head">
          <span>测绘控制汇总</span>
          <button class="side-refresh" type="button" title="重新拉取聚合快照" @click="refreshSummary(true)">
            刷新
          </button>
        </header>
        <ul class="side-summary-list">
          <li v-for="card in aggregateStore.summaryCards" :key="card.label">
            <span>{{ card.label }}</span>
            <strong>{{ card.value }}</strong>
          </li>
        </ul>
        <p v-if="aggregateStore.errorMessage" class="side-summary-error">{{ aggregateStore.errorMessage }}</p>
        <p class="side-summary-foot">
          快照 v{{ aggregateStore.aggregate.version }}<template v-if="!consistencyOk"> · 分段不一致</template>
        </p>
      </section>
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
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useSessionStore } from '@/stores/session'
import { useSurveyAggregateStore } from '@/stores/surveyAggregate'

const store = useSessionStore()
const aggregateStore = useSurveyAggregateStore()
const route = useRoute()

const consistencyOk = computed(() => aggregateStore.consistencyOk)

function refreshSummary(force = false) {
  void aggregateStore.refresh({ force })
}

// 首次进入拉一次；之后每次切换页面都重新拉，避免复核后侧栏还停在旧快照。
refreshSummary(true)
watch(() => route.path, () => refreshSummary(true))

const navItems = [{ label: "运营概览", path: "/" }, { label: "钻孔编录", path: "/borehole" }, { label: "岩心管理", path: "/core" }, { label: "地层划分", path: "/stratigraphy" }, { label: "地球物理", path: "/geophysics" }, { label: "化探分析", path: "/geochem" }, { label: "化验数据", path: "/assay" }, { label: "地质填图", path: "/mapping" }, { label: "测绘控制", path: "/survey_point" }, { label: "点位图工作台", path: "/survey_point/workbench" }, { label: "钻探日志", path: "/drilling_log" }, { label: "储量估算", path: "/reserve" }, { label: "样品登记", path: "/sample_registry" }, { label: "勘探设备", path: "/equipment" }, { label: "水文地质", path: "/hydro" }, { label: "剖面编录", path: "/section" }, { label: "地质报告", path: "/geological_report" }, { label: "遥感解译", path: "/remote" }, { label: "矿产评价", path: "/mineral" }, { label: "环境地质", path: "/environmental" }]
</script>
