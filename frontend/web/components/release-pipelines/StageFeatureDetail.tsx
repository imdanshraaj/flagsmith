import { getProjectFlag } from 'common/services/useProjectFlag'
import { getStore } from 'common/store'
import { ProjectFlag } from 'common/types/responses'
import { useCallback, useEffect, useState } from 'react'
type StageFeatureDetailProps = {
  features: number[]
  projectId: number
}

const StageFeatureDetail = ({
  features,
  projectId,
}: StageFeatureDetailProps) => {
  const [projectFlags, setProjectFlags] = useState<ProjectFlag[]>([])
  const getProjectFlags = useCallback(async () => {
    if (!features.length) {
      return
    }

    try {
      const res = await Promise.all(
        features.map((id) =>
          getProjectFlag(getStore(), {
            id: `${id}`,
            project: projectId,
          }),
        ),
      )
      const projectFlags = res.map((r) => r.data)
      setProjectFlags(projectFlags)
    } catch (error) {
      console.error(error)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features?.join(','), projectId])

  useEffect(() => {
    getProjectFlags()
  }, [getProjectFlags])

  if (!features.length) {
    return (
      <>
        <h6>Features (0)</h6>
        <p className='text-muted'>No features at this stage.</p>
      </>
    )
  }

  return (
    <>
      <h6>Features ({features.length})</h6>
      {projectFlags?.map((flag) => (
        <p key={flag.id} className='text-muted'>
          <b>{flag.name}</b>
        </p>
      ))}
    </>
  )
}

export default StageFeatureDetail
