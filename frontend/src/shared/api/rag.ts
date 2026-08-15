import type {
  RagAnswerRequest,
  RagAnswerResponse,
} from '../../entities/paper/model'
import { requestJson } from './http'

export function answerRag(
  request: RagAnswerRequest,
): Promise<RagAnswerResponse> {
  return requestJson<RagAnswerResponse>('/rag/answer', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}
