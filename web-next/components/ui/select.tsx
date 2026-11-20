"use client"

import * as React from "react"
import { Select as SelectPrimitive } from "@base-ui/react/select"
import { CheckIcon, ChevronDownIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export type SelectOption = {
  value: string
  label: React.ReactNode
}

type SelectProps = {
  id?: string
  value: string | null
  options: SelectOption[]
  onValueChange: (value: string) => void
  disabled?: boolean
  placeholder?: string
  emptyText?: string
  className?: string
  popupClassName?: string
  name?: string
  "aria-labelledby"?: string
}

function Select({
  id,
  value,
  options,
  onValueChange,
  disabled = false,
  placeholder = "请选择",
  emptyText = "没有可选项",
  className,
  popupClassName,
  name,
  "aria-labelledby": ariaLabelledBy,
}: SelectProps) {
  const items = React.useMemo(
    () => options.map((option) => ({ label: option.label, value: option.value })),
    [options]
  )

  return (
    <SelectPrimitive.Root
      name={name}
      value={value}
      items={items}
      disabled={disabled}
      onValueChange={(next) => {
        if (typeof next === "string") onValueChange(next)
      }}
    >
      <SelectPrimitive.Trigger
        id={id}
        data-slot="select-trigger"
        aria-labelledby={ariaLabelledBy}
        className={cn(
          "group flex h-10 w-full min-w-0 items-center gap-2 rounded-lg border border-input bg-background px-2.5 text-left text-sm outline-none",
          "transition-[border-color,background-color,box-shadow] duration-150",
          "hover:border-foreground/20 hover:bg-foreground/[0.015]",
          "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
          "data-[popup-open]:border-foreground/40 data-[popup-open]:bg-background",
          "data-[popup-open]:shadow-[0_0_0_4px_rgb(0_0_0/0.05)]",
          "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
      >
        <SelectPrimitive.Value
          className="min-w-0 flex-1 truncate data-[placeholder]:text-muted-foreground"
          placeholder={placeholder}
        />
        <ChevronDownIcon
          aria-hidden
          className="text-muted-foreground size-3.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] group-data-[popup-open]:rotate-180 group-data-[popup-open]:text-foreground"
        />
      </SelectPrimitive.Trigger>

      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner
          sideOffset={6}
          alignItemWithTrigger={false}
          className="z-50 outline-none"
        >
          <SelectPrimitive.Popup
            data-slot="select-popup"
            className={cn(
              "bg-popover text-popover-foreground max-h-[min(20rem,var(--available-height))] min-w-[var(--anchor-width)] overflow-y-auto rounded-xl p-[5px] text-sm",
              "ring-1 ring-foreground/10 shadow-[0_16px_40px_-12px_rgb(0_0_0/0.18)]",
              "origin-[var(--transform-origin)] transition-[opacity,transform] duration-150 ease-[cubic-bezier(0.16,1,0.3,1)]",
              "data-[starting-style]:scale-[0.97] data-[starting-style]:opacity-0 data-[starting-style]:-translate-y-1.5",
              "data-[ending-style]:scale-[0.98] data-[ending-style]:opacity-0",
              popupClassName
            )}
          >
            {options.length ? (
              <>
                <SelectPrimitive.List className="flex flex-col outline-none">
                  {options.map((option, index) => (
                    <SelectPrimitive.Item
                      key={option.value}
                      value={option.value}
                      className={cn(
                        "group flex h-9 w-full cursor-default items-center gap-2 rounded-lg px-2.5 text-left text-sm outline-none select-none",
                        "transition-colors duration-100",
                        "data-[highlighted]:bg-foreground/[0.05]",
                        "data-[selected]:bg-foreground/[0.04]",
                        "motion-safe:animate-[select-item-in_280ms_cubic-bezier(0.16,1,0.3,1)_both] motion-reduce:animate-none"
                      )}
                      style={{ animationDelay: `${Math.min(index, 8) * 22}ms` }}
                    >
                      <SelectPrimitive.ItemText className="min-w-0 flex-1 truncate">
                        {option.label}
                      </SelectPrimitive.ItemText>
                      <CheckIcon
                        aria-hidden
                        className={cn(
                          "size-3.5 shrink-0 scale-50 opacity-0",
                          "transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.34,1.56,0.64,1)]",
                          "group-data-[selected]:scale-100 group-data-[selected]:opacity-100"
                        )}
                      />
                    </SelectPrimitive.Item>
                  ))}
                </SelectPrimitive.List>
              </>
            ) : (
              <p className="text-muted-foreground px-2 py-3 text-xs">{emptyText}</p>
            )}
          </SelectPrimitive.Popup>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}

export { Select }
