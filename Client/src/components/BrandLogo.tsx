import { Activity } from 'lucide-react'

export function BrandLogo({ light = false, className = '' }: { light?: boolean; className?: string }) {
  return (
    <div className={`flex min-w-0 items-center gap-2.5 ${className}`}>
      <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-green-800 to-lime-600 text-white shadow-sm shadow-green-950/20">
        <Activity size={18} strokeWidth={2.5} />
      </span>
      <span className={`min-w-0 flex-1 text-[12px] font-extrabold leading-4 tracking-[-0.035em] [overflow-wrap:anywhere] ${light ? 'text-white' : 'text-[#182334]'}`}>
        IndustrialAnomalyDetection
      </span>
    </div>
  )
}
