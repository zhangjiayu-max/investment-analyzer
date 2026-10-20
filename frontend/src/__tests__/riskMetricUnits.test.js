import { expect,it } from 'vitest'
import { formatRiskRatio, volatilityColorClass } from '../components/market/eventRadar/helpers'
it('formats portfolio risk ratios as percentage points and unknown as unknown',()=>{expect(formatRiskRatio(.1234)).toBe('12.34%');expect(formatRiskRatio(null)).toBe('—');expect(formatRiskRatio(NaN)).toBe('—')})
it('uses ratio thresholds for annual volatility colors',()=>{expect(volatilityColorClass(.12)).toBe('risk-good');expect(volatilityColorClass(.2)).toBe('risk-warn');expect(volatilityColorClass(.3)).toBe('risk-bad')})
