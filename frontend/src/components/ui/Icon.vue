<script setup>
/**
 * Icon 统一图标组件（lucide-vue-next 适配层）
 *
 * 使用方式：<Icon name="dashboard" size="20" />
 *
 * 内部把语义 name 映射到 lucide 图标组件，调用点零改动。
 * 缺失 name 走 CircleAlert fallback，扩展只需在 LUCIDE_MAP 加映射项。
 * 统一 stroke-width=1.75、24x24 viewBox、线性图标语言。
 *
 * 注意：显式 import 各图标组件以支持 tree-shaking，
 * 切勿改回 `import * as icons`（会使 bundle 暴增 600KB+）。
 */
import { computed } from 'vue'
import {
  LayoutDashboard, MessageCircle, Newspaper, BarChart3, Image, Briefcase, Server,
  Coins, Settings, Flame, BookOpen, TrendingUp, Zap, Link, Search, Bug,
  CheckCircle2, ChevronDown, ChevronUp, ChevronRight, ChevronLeft,
  RefreshCw, X, Plus, Minus, Send, MoreHorizontal, ScanSearch, Wrench,
  Pencil, Trash2, ExternalLink, Download, Upload, Eye, Clock, Calendar,
  Tag, Paperclip, Star, StarOff, Inbox, Maximize2, Minimize2,
  AlertTriangle, Info, XCircle, Loader2, Hourglass, AlarmClock, Square,
  Siren, ShieldAlert, ShieldCheck, TrendingDown,
  ArrowUp, ArrowDown, ArrowRight, ArrowLeft,
  CandlestickChart, LineChart, PieChart, Gauge, Percent,
  Wallet, CircleDollarSign, Banknote, Landmark, Scale, Activity, HeartPulse,
  Bot, Brain, Lightbulb, Sparkles, Globe, FlaskConical, Microscope,
  Library, PenLine, Target, Tv, ClipboardList, MessageSquareDot,
  CircleUser, User, ThumbsUp, ThumbsDown, FileText, Users,
  Sun, Moon, Circle, CircleAlert,
} from 'lucide-vue-next'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
  color: { type: String, default: '' },        // 覆盖颜色，空则继承当前文字色
  strokeWidth: { type: [Number, String], default: 1.75 },
})

// 语义 name → lucide 组件 映射表
const LUCIDE_MAP = {
  // 导航类
  dashboard: LayoutDashboard, chat: MessageCircle, articles: Newspaper,
  valuation: BarChart3, gallery: Image, portfolio: Briefcase, admin: Server,
  token: Coins, config: Settings, fire: Flame, author: BookOpen, book: BookOpen,
  bond: TrendingUp, evolution: Zap, link: Link, rag: Search, search: Search,
  bug: Bug, check: CheckCircle2, chart: BarChart3, newspaper: Newspaper,
  // 操作类
  'chevron-down': ChevronDown, 'chevron-up': ChevronUp,
  'chevron-right': ChevronRight, 'chevron-left': ChevronLeft,
  refresh: RefreshCw, close: X, plus: Plus, minus: Minus, send: Send,
  more: MoreHorizontal, 'scan-search': ScanSearch, wrench: Wrench,
  pencil: Pencil, trash: Trash2, 'external-link': ExternalLink,
  download: Download, upload: Upload, eye: Eye, clock: Clock,
  calendar: Calendar, tag: Tag, paperclip: Paperclip, star: Star,
  'star-off': StarOff, inbox: Inbox, maximize: Maximize2, minimize: Minimize2,
  // 状态类
  warning: AlertTriangle, info: Info, success: CheckCircle2, error: XCircle,
  spinner: Loader2, hourglass: Hourglass, 'alarm-clock': AlarmClock,
  square: Square, siren: Siren, 'shield-alert': ShieldAlert,
  'shield-check': ShieldCheck, shield: ShieldCheck,
  // 金融类
  'trending-up': TrendingUp, 'trending-down': TrendingDown,
  'arrow-up': ArrowUp, 'arrow-down': ArrowDown, 'arrow-right': ArrowRight,
  'arrow-left': ArrowLeft,
  'candlestick-chart': CandlestickChart, 'line-chart': LineChart,
  'pie-chart': PieChart, gauge: Gauge, percent: Percent,
  wallet: Wallet, coins: Coins, 'circle-dollar-sign': CircleDollarSign,
  banknote: Banknote, landmark: Landmark, scale: Scale,
  activity: Activity, 'heart-pulse': HeartPulse,
  // AI / 内容类
  bot: Bot, brain: Brain, lightbulb: Lightbulb, sparkles: Sparkles,
  globe: Globe, flask: FlaskConical, 'flask-conical': FlaskConical,
  microscope: Microscope, library: Library, 'book-open': BookOpen,
  'pen-line': PenLine, target: Target, tv: Tv,
  'clipboard-list': ClipboardList, 'message-circle': MessageCircle,
  'message-square-dot': MessageSquareDot,
  'circle-user': CircleUser, user: User,
  'thumbs-up': ThumbsUp, 'thumbs-down': ThumbsDown,
  'file-text': FileText, users: Users,
  // 主题
  sun: Sun, moon: Moon,
  // 杂项
  circle: Circle, test: FlaskConical,
}

const Comp = computed(() => LUCIDE_MAP[props.name] || CircleAlert)
const isSpinning = computed(() => props.name === 'spinner')
</script>

<template>
  <component
    :is="Comp"
    :size="Number(size) || 20"
    :color="color || undefined"
    :stroke-width="Number(strokeWidth) || 1.75"
    :class="['icon', `icon--${name}`, { spinning: isSpinning }]"
  />
</template>

<style scoped>
.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.icon.spinning {
  animation: icon-spin 0.8s linear infinite;
}
@keyframes icon-spin {
  to { transform: rotate(360deg); }
}
</style>
