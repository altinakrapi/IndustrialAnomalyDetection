export function FormField({ label, name, required, placeholder, type = 'text', defaultValue }: { label: string; name: string; required?: boolean; placeholder?: string; type?: string; defaultValue?: string | number }) {
  return <label>{label}<input name={name} required={required} placeholder={placeholder} type={type} defaultValue={defaultValue} /></label>
}
