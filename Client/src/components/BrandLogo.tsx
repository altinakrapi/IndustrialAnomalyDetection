import { Activity } from 'lucide-react'

export function BrandLogo({ light = false, className = '' }: { light?: boolean; className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-green-800 to-lime-600 text-white shadow-sm shadow-green-950/20">
        <Activity size={18} strokeWidth={2.5} />
      </span>
      <span className={`text-[13px] font-extrabold tracking-[-0.04em] ${light ? 'text-white' : 'text-[#182334]'}`}>
        IndustrialAnomalyDetection
      </span>
    </div>
  )
}
