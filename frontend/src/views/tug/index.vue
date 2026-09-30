<template>
  <section class="page" data-module="tug">
    <header class="page-head">
      <div>
        <h2>拖轮调度</h2>
        <p class="page-desc">按水域与船型口径算定马力需求再派工：可用马力不足直接拦下并说明差额，作业窗口越界单独提示，检修拖轮不参与派工。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openDispatch">登记派工</button>
        <button class="btn" type="button" @click="reloadAll">刷新看板</button>
      </div>
    </header>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <footer v-if="errorMessage" class="error-banner">{{ errorMessage }}</footer>
    <footer v-if="successMessage" class="success-banner">{{ successMessage }}</footer>

    <!-- 调度看板 -->
    <div v-if="activeTab === 'board'">
      <div class="stat-row">
        <article v-for="card in board.cards" :key="card.label" class="stat-card">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
        </article>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>水域</th><th>允许作业时段</th><th>在途需求马力</th><th>在途派出马力</th>
            <th>在途派工单</th><th>待命/作业/检修（条）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in board.waters" :key="row.水域">
            <td>{{ row.水域 }}</td>
            <td>{{ row.允许时段 }}</td>
            <td>{{ row.需求马力合计 }}</td>
            <td>{{ row.派出马力合计 }}</td>
            <td>{{ row.在途派工单 }}</td>
            <td>{{ row.待命拖轮 }} / {{ row.作业拖轮 }} / {{ row.检修拖轮 }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">在途派工单（看板读到的需求马力与台账、派工单同源）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>派工单号</th><th>作业号</th><th>船名</th><th>船型</th><th>水域</th>
            <th>需求马力</th><th>需求条数</th><th>派出马力</th><th>拖轮</th><th>状态</th><th>版本</th><th>窗口提示</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in board.active_orders" :key="row.派工单号">
            <td>{{ row.派工单号 }}</td>
            <td>{{ row.作业号 }}</td>
            <td>{{ row.船名 }}</td>
            <td>{{ row.船型 }}</td>
            <td>{{ row.水域 }}</td>
            <td>{{ row.需求马力 }}</td>
            <td>{{ row.需求条数 }}</td>
            <td>{{ row.派出马力 }}</td>
            <td>{{ row.拖轮编号列表.join('、') }}</td>
            <td>{{ row.状态 }}</td>
            <td>v{{ row.版本 }}</td>
            <td :class="{ 'warn-text': row.窗口提示 }">{{ row.窗口提示 || '—' }}</td>
          </tr>
          <tr v-if="!board.active_orders?.length">
            <td colspan="12" class="empty-state">暂无在途派工单</td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">作业窗口越界提示</h3>
      <table class="data-table">
        <thead><tr><th>派工单号</th><th>作业号</th><th>船名</th><th>水域</th><th>提示</th></tr></thead>
        <tbody>
          <tr v-for="row in board.window_alerts" :key="row.派工单号">
            <td>{{ row.派工单号 }}</td><td>{{ row.作业号 }}</td><td>{{ row.船名 }}</td><td>{{ row.水域 }}</td>
            <td class="warn-text">{{ row.窗口提示 }}</td>
          </tr>
          <tr v-if="!board.window_alerts?.length">
            <td colspan="5" class="empty-state">暂无窗口越界作业</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 拖轮台账 -->
    <div v-else-if="activeTab === 'tugs'">
      <form class="filter-bar" @submit.prevent="loadTugs">
        <label class="filter-item">
          <span>拖轮编号/船名</span>
          <input v-model="tugFilters.keyword" placeholder="按编号或船名检索" />
        </label>
        <label class="filter-item">
          <span>水域</span>
          <select v-model="tugFilters.water">
            <option value="">全部水域</option>
            <option v-for="w in waters" :key="w" :value="w">{{ w }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="tugFilters.status">
            <option value="">全部状态</option>
            <option v-for="s in tugStatuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetTugFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr><th v-for="col in tugColumns" :key="col">{{ col }}</th><th>可执行动作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in tugs" :key="String(row.id)">
            <td v-for="col in tugColumns" :key="col">{{ row[col] || '—' }}</td>
            <td class="row-actions">
              <button v-if="row.status !== '检修中'" class="link" type="button" @click="runTugAction('标记检修', row)">标记检修</button>
              <button v-else class="link" type="button" @click="runTugAction('解除检修', row)">解除检修</button>
            </td>
          </tr>
          <tr v-if="!tugs.length">
            <td :colspan="tugColumns.length + 1" class="empty-state">暂无符合条件的拖轮</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot"><span>共 {{ tugTotal }} 条拖轮台账</span></footer>
    </div>

    <!-- 派工单 -->
    <div v-else>
      <form class="filter-bar" @submit.prevent="loadOrders">
        <label class="filter-item">
          <span>作业号/派工单号/船名</span>
          <input v-model="orderFilters.keyword" placeholder="按关键字检索" />
        </label>
        <label class="filter-item">
          <span>水域</span>
          <select v-model="orderFilters.water">
            <option value="">全部水域</option>
            <option v-for="w in waters" :key="w" :value="w">{{ w }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="orderFilters.status">
            <option value="">全部状态</option>
            <option v-for="s in orderStatuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetOrderFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr><th v-for="col in orderColumns" :key="col">{{ col }}</th><th>可执行动作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in orders" :key="String(row.id)">
            <td v-for="col in orderColumns" :key="col">
              <span v-if="col === '拖轮编号列表'">{{ (row[col] || []).join('、') }}</span>
              <span v-else-if="col === 'version'">v{{ row[col] }}</span>
              <span v-else-if="col === '窗口提示'" :class="{ 'warn-text': row[col] }">{{ row[col] || '—' }}</span>
              <span v-else>{{ row[col] ?? '—' }}</span>
            </td>
            <td class="row-actions">
              <button v-if="row.status === '已派工'" class="link" type="button" @click="runOrderAction('开始作业', row)">开始作业</button>
              <button v-if="row.status === '已派工' || row.status === '作业中'" class="link" type="button" @click="runOrderAction('完成作业', row)">完成作业</button>
              <button v-if="row.status === '已派工' || row.status === '作业中'" class="link danger" type="button" @click="runOrderAction('取消派工', row)">取消派工</button>
            </td>
          </tr>
          <tr v-if="!orders.length">
            <td :colspan="orderColumns.length + 1" class="empty-state">暂无派工单，可先登记派工</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot"><span>共 {{ orderTotal }} 张派工单</span></footer>
    </div>

    <!-- 派工试算弹窗：先看口径结果，再决定是否落库 -->
    <div v-if="dispatchOpen" class="modal-mask" @click.self="dispatchOpen = false">
      <div class="modal">
        <h3>登记派工</h3>
        <div class="form-grid">
          <label><span>作业号 *</span><input v-model="form.作业号" placeholder="如 JOB-20260930-02" /></label>
          <label><span>船名 *</span><input v-model="form.船名" /></label>
          <label>
            <span>船型 *</span>
            <select v-model="form.船型">
              <option value="">请选择</option>
              <option v-for="t in vesselTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <label>
            <span>水域 *</span>
            <select v-model="form.水域">
              <option value="">请选择</option>
              <option v-for="w in waters" :key="w" :value="w">{{ w }}</option>
            </select>
          </label>
          <label><span>拖带作业量（吨）*</span><input v-model.number="form.拖带作业量" type="number" min="1" /></label>
          <label><span>作业开始 *</span><input v-model="form.作业开始" type="datetime-local" /></label>
          <label><span>作业结束 *</span><input v-model="form.作业结束" type="datetime-local" /></label>
          <label>
            <span>指定拖轮（可留空自动选配）</span>
            <input v-model="manualTugs" placeholder="多个编号用逗号分隔，留空自动派" />
          </label>
        </div>

        <div class="preview-box" v-if="plan">
          <div class="preview-line">
            口径：基准 {{ plan.base_hp }} 马力 + 每千吨 {{ plan.hp_per_kt }} 马力
            → 需求 <strong>{{ plan.required_hp }}</strong> 马力 /
            <strong>{{ plan.required_count }}</strong> 条
          </div>
          <div class="preview-line">拟派马力：{{ plan.available_hp }}，缺口：<span :class="plan.hp_gap ? 'warn-text' : ''">{{ plan.hp_gap }}</span></div>
          <div class="preview-line">推荐拖轮：{{ plan.recommended_codes.join('、') || '（无）' }}</div>
          <div class="preview-line">允许时段：{{ plan.allowed_window }}</div>
          <div v-if="plan.window_warning" class="warn-text">⚠ {{ plan.window_warning }}</div>
          <ul v-if="plan.blockers.length" class="block-list">
            <li v-for="(b, i) in plan.blockers" :key="i" class="warn-text">✋ {{ b }}</li>
          </ul>
          <div v-if="plan.existing_order" class="info-text">
            同一作业已有派工单 {{ plan.existing_order.派工单号 }}（v{{ plan.existing_order.version }}），正式派工将升版为最新一版
          </div>
        </div>

        <div class="modal-foot">
          <button class="btn" type="button" @click="preview">按口径试算</button>
          <button class="btn primary" type="button" :disabled="!plan || !plan.can_dispatch" @click="confirmDispatch">
            正式派工
          </button>
          <button class="btn ghost" type="button" @click="dispatchOpen = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Board = {
  cards: { label: string; value: number }[]
  waters: Record<string, any>[]
  active_orders: Record<string, any>[]
  window_alerts: Record<string, any>[]
}
type Plan = {
  required_hp: number
  required_count: number
  base_hp: number
  hp_per_kt: number
  available_hp: number
  hp_gap: number
  within_window: boolean
  allowed_window: string
  window_warning: string
  blockers: string[]
  can_dispatch: boolean
  recommended_codes: string[]
  existing_order: { 派工单号: string; version: number } | null
} | null

const ENDPOINT = '/api/tug'
const tabs = [
  { key: 'board', label: '调度看板' },
  { key: 'tugs', label: '拖轮台账' },
  { key: 'orders', label: '派工单' },
]
const waters = ['内港池', '主航道', '锚地']
const vesselTypes = ['小型船', '中型船', '大型船', '超大型船']
const tugStatuses = ['待命', '作业中', '检修中']
const orderStatuses = ['已派工', '作业中', '已完成', '已取消']
const tugColumns = ['拖轮编号', '船名', '额定马力', '水域', '作业号', '需求马力', 'status']
const orderColumns = [
  '派工单号', '作业号', '船名', '船型', '水域', '拖带作业量',
  '作业开始', '作业结束', '需求马力', '需求条数', '派出马力', '拖轮编号列表',
  'status', 'version', '窗口提示',
]

const activeTab = ref('board')
const errorMessage = ref('')
const successMessage = ref('')

const board = ref<Board>({ cards: [], waters: [], active_orders: [], window_alerts: [] })
const tugs = ref<Row[]>([])
const tugTotal = ref(0)
const orders = ref<Row[]>([])
const orderTotal = ref(0)
const tugFilters = reactive({ keyword: '', water: '', status: '' })
const orderFilters = reactive({ keyword: '', water: '', status: '' })

const dispatchOpen = ref(false)
const manualTugs = ref('')
const plan = ref<Plan>(null)
const form = reactive({
  作业号: '',
  船名: '',
  船型: '',
  水域: '',
  拖带作业量: undefined as number | undefined,
  作业开始: '',
  作业结束: '',
})

function flashError(message: string) {
  errorMessage.value = message
  successMessage.value = ''
}

function flashSuccess(message: string) {
  successMessage.value = message
  errorMessage.value = ''
  window.setTimeout(() => { successMessage.value = '' }, 4000)
}

async function loadBoard() {
  try {
    const resp = await request(`${ENDPOINT}/board`)
    if (!resp.ok) throw new Error('调度看板读取失败')
    board.value = await resp.json()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '调度看板读取失败')
  }
}

async function loadTugs() {
  const query = new URLSearchParams()
  if (tugFilters.keyword) query.set('keyword', tugFilters.keyword)
  if (tugFilters.water) query.set('water', tugFilters.water)
  if (tugFilters.status) query.set('status', tugFilters.status)
  try {
    const resp = await request(`${ENDPOINT}?${query.toString()}`)
    if (!resp.ok) throw new Error('拖轮台账读取失败')
    const payload = await resp.json()
    tugs.value = payload.items ?? []
    tugTotal.value = payload.total ?? tugs.value.length
  } catch (error) {
    flashError(error instanceof Error ? error.message : '拖轮台账读取失败')
  }
}

function resetTugFilters() {
  tugFilters.keyword = ''
  tugFilters.water = ''
  tugFilters.status = ''
  void loadTugs()
}

async function loadOrders() {
  const query = new URLSearchParams()
  if (orderFilters.keyword) query.set('keyword', orderFilters.keyword)
  if (orderFilters.water) query.set('water', orderFilters.water)
  if (orderFilters.status) query.set('status', orderFilters.status)
  try {
    const resp = await request(`${ENDPOINT}/orders/list?${query.toString()}`)
    if (!resp.ok) throw new Error('派工单读取失败')
    const payload = await resp.json()
    orders.value = payload.items ?? []
    orderTotal.value = payload.total ?? orders.value.length
  } catch (error) {
    flashError(error instanceof Error ? error.message : '派工单读取失败')
  }
}

function resetOrderFilters() {
  orderFilters.keyword = ''
  orderFilters.water = ''
  orderFilters.status = ''
  void loadOrders()
}

async function runTugAction(action: string, row: Row) {
  try {
    const resp = await request(`${ENDPOINT}/${String(row.id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await resp.json()
    if (!resp.ok || !payload.ok) throw new Error(payload.message || '拖轮操作未生效')
    flashSuccess(payload.message)
    await Promise.all([loadTugs(), loadBoard()])
  } catch (error) {
    flashError(error instanceof Error ? error.message : '拖轮操作失败')
  }
}

async function runOrderAction(action: string, row: Row) {
  try {
    const resp = await request(`${ENDPOINT}/orders/${String(row.id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await resp.json()
    if (!resp.ok || !payload.ok) throw new Error(payload.message || '派工单操作未生效')
    flashSuccess(payload.message)
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '派工单操作失败')
  }
}

function openDispatch() {
  plan.value = null
  manualTugs.value = ''
  dispatchOpen.value = true
}

function buildPayload() {
  return {
    作业号: form.作业号.trim(),
    船名: form.船名.trim(),
    船型: form.船型,
    水域: form.水域,
    拖带作业量: Number(form.拖带作业量),
    作业开始: form.作业开始,
    作业结束: form.作业结束,
    拖轮编号: manualTugs.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
  }
}

async function preview() {
  errorMessage.value = ''
  try {
    const resp = await request(`${ENDPOINT}/dispatch/preview`, {
      method: 'POST',
      body: JSON.stringify(buildPayload()),
    })
    const payload = await resp.json()
    if (!resp.ok) throw new Error(payload.detail || '试算失败')
    plan.value = payload.plan as Plan
  } catch (error) {
    plan.value = null
    flashError(error instanceof Error ? error.message : '试算失败')
  }
}

async function confirmDispatch() {
  try {
    const resp = await request(`${ENDPOINT}/dispatch`, {
      method: 'POST',
      body: JSON.stringify(buildPayload()),
    })
    const payload = await resp.json()
    if (!resp.ok || !payload.ok) throw new Error(payload.message || '派工未生效')
    dispatchOpen.value = false
    flashSuccess(payload.message)
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '派工失败')
  }
}

function switchTab(key: string) {
  activeTab.value = key
  if (key === 'tugs') void loadTugs()
  if (key === 'orders') void loadOrders()
  if (key === 'board') void loadBoard()
}

async function reloadAll() {
  await Promise.all([loadBoard(), loadTugs(), loadOrders()])
}

onMounted(reloadAll)
</script>

<style scoped>
.tabs { display: flex; gap: 8px; margin: 8px 0 12px; }
.tab { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
.tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.block-title { font-size: 14px; margin: 18px 0 8px; }
.warn-text { color: #b54708; }
.info-text { color: var(--brand); font-size: 12px; margin-top: 6px; }
.link.danger { color: #b42318; }
.error-banner { background: #fef3f2; border: 1px solid #fecdca; color: #b42318; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; }
.success-banner { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 720px; max-width: 92vw; max-height: 88vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.form-grid label span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.form-grid input, .form-grid select { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.preview-box { margin-top: 14px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: #f8fafc; }
.preview-line { font-size: 13px; line-height: 1.9; }
.block-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.modal-foot button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
