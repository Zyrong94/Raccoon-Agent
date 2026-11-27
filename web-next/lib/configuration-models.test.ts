import {expect, it} from 'vitest';
import {modelDraft, providerDraft, providerModelConfig, type ConfigurationRecord} from './configuration';

it('round trips the selected reasoning effort as a model field', () => {
 const record:ConfigurationRecord={id:'custom/gpt-6-luna',revision:'1',source:'managed',config:{
  provider_id:'custom',model_id:'gpt-6-luna',supported_reasoning_efforts:['low','high'],reasoning_effort:'high',
 }};
 const model=modelDraft(record);
 const provider={...providerDraft(),id:'custom'};
 model.reasoningEffort='low';
 const saved=providerModelConfig(provider,model);
 expect(saved.reasoning_effort).toBe('low');
 expect(saved).not.toHaveProperty('request');
 expect(saved).not.toHaveProperty('extra_body');
 expect(saved).not.toHaveProperty('temperature');
 expect(saved).not.toHaveProperty('max_tokens');
 model.reasoningEffort='';
 expect(providerModelConfig(provider,model)).not.toHaveProperty('reasoning_effort');
});
