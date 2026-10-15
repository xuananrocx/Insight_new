import type { Provider } from './account-api'

export function modelChoiceValue(providerId: string, modelId: string) {
  return JSON.stringify([providerId, modelId])
}

export function modelChoiceFromValue(value: string, providers: Provider[]) {
  // Radix's hidden native select can emit an empty value while options load.
  // Accept only an actual, currently usable option; never parse arbitrary input.
  for (const provider of providers) {
    if (!provider.usable) continue
    for (const model of provider.models) {
      if (value === modelChoiceValue(provider.id, model.model_id)) {
        return { providerId: provider.id, modelId: model.model_id }
      }
    }
  }
  return null
}
