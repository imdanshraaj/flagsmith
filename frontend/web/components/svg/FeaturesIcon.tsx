import React from 'react'

interface FeaturesIconProps {
  className?: string
}

const FeaturesIcon: React.FC<FeaturesIconProps> = ({ className }) => {
  return (
    <svg className={className} viewBox='0 0 21 21'>
      <path
        d='M20.207.772a.726.726 0 00-.498-.497C18.425 0 17.419 0 16.417 0c-4.129 0-6.605 2.208-8.453 5.12H3.796c-.652 0-1.424.477-1.716 1.06L.103 10.132c-.061.135-.095.28-.1.428 0 .53.43.96.96.96H5.12a3.84 3.84 0 013.84 3.84v4.16c0 .53.43.96.96.96.148-.005.294-.04.429-.1l3.95-1.977c.582-.291 1.059-1.062 1.059-1.714v-4.177c2.903-1.853 5.12-4.336 5.12-8.444.004-1.006.004-2.012-.272-3.296zM15.362 6.72a1.6 1.6 0 110-3.2 1.6 1.6 0 010 3.2zM1.427 14.083C.393 15.117-.118 17.703.023 20.456c2.765.143 5.345-.374 6.375-1.404 1.611-1.611 1.715-3.76.252-5.222-1.463-1.462-3.611-1.359-5.223.253zm3.266 3.36c-.344.344-1.204.516-2.125.469-.047-.918.123-1.78.468-2.124.537-.538 1.253-.572 1.74-.085.488.488.454 1.204-.083 1.74z'
        fill='#FFF'
        fillRule='evenodd'
      />
    </svg>
  )
}

export default FeaturesIcon
